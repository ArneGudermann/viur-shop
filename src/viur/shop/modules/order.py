import logging
import time
import typing as t

from viur.core import current, db, errors as core_errors, exposed, force_post
from viur.core.prototypes import List
from .abstract import ShopModuleAbstract
from .. import exceptions as e
from ..constants import AddressType
from ..payment_providers import PaymentProviderAbstract
from ..response_types import JsonResponse
from ..skeletons.order import get_payment_providers

if t.TYPE_CHECKING:
    from viur.core.skeleton import SkeletonInstance

logger = logging.getLogger("viur.shop").getChild(__name__)

_sentinel = object()


class Order(ShopModuleAbstract, List):
    kindName = "shop_order"

    @exposed
    def payment_providers_list(self):
        return JsonResponse(get_payment_providers())

    def order_add(
        self,
        cart_key: db.Key,
        payment_provider: str = _sentinel,
        billing_address_key: db.Key = _sentinel,
        email: str = _sentinel,
        customer_key: db.Key = _sentinel,
        state_ordered: bool = _sentinel,
        state_paid: bool = _sentinel,
        state_rts: bool = _sentinel,
    ):
        if not isinstance(cart_key, db.Key):
            raise TypeError(f"cart_key must be an instance of db.Key")
        if billing_address_key is not _sentinel and not isinstance(billing_address_key, (db.Key, type(None))):
            raise TypeError(f"billing_address_key must be an instance of db.Key")
        if customer_key is not _sentinel and not isinstance(customer_key, (db.Key, type(None))):
            raise TypeError(f"customer_key must be an instance of db.Key")
        skel = self.addSkel()
        cart_skel = self.shop.cart.viewSkel("node")
        if not self.shop.cart.is_valid_node(cart_key, root_node=True):
            raise ValueError(f"Invalid {cart_key=}!")
        assert cart_skel.fromDB(cart_key)
        skel.setBoneValue("cart", cart_key)
        skel["total"] = cart_skel["total"]
        if payment_provider is not _sentinel:
            skel["payment_provider"] = payment_provider  # TODO: validate
        if billing_address_key is not _sentinel:
            if billing_address_key is None:
                skel["billing_address"] = None
            else:
                skel.setBoneValue("billing_address", billing_address_key)
                if skel["billing_address"]["dest"]["address_type"] != AddressType.BILLING:
                    raise e.InvalidArgumentException(
                        "shipping_address",
                        descr_appendix="Address is not of type billing."
                    )
        if user := current.user.get():
            # us current user as default value
            skel["email"] = user["name"]
            skel.setBoneValue("customer", user["key"])
        if email is not _sentinel:
            skel["email"] = email
        if customer_key is not _sentinel:
            skel.setBoneValue("customer", customer_key)  # TODO: validate (must be self of an admin)
        # TODO(discussion): Do we really want to set this by the frontend?
        #  Or what are the pre conditions?
        if state_ordered is not _sentinel:
            skel["state_ordered"] = state_ordered
        if state_paid is not _sentinel:
            skel["state_paid"] = state_paid
        if state_rts is not _sentinel:
            skel["state_rts"] = state_rts
        skel.toDB()
        if cart_key == self.shop.cart.current_session_cart_key:
            # This is now an order basket and should no longer be modified
            self.shop.cart.detach_session_cart()
        return skel

    @exposed
    # @force_post
    def checkout_start(
        self,
        order_key: db.Key,
    ):
        order_key = self.shop.api._normalize_external_key(order_key, "order_key")
        if not isinstance(order_key, db.Key):
            raise TypeError(f"order_key must be an instance of db.Key")
        skel = self.editSkel()
        if not skel.fromDB(order_key):
            raise core_errors.NotFound()
        if errors := self.can_checkout(skel):
            logging.error(errors)
            return JsonResponse({
                "errors": errors,
            }, status_code=400)
            raise e.InvalidStateError(", ".join(errors))

        skel = self.freeze_order(skel)
        skel = self.assign_uid(skel)
        skel.toDB()
        return JsonResponse(skel)

    def can_checkout(
        self,
        order_skel: "SkeletonInstance",
    ) -> list["ErrorClassTBD"]:  # TODO
        errors = []
        if not order_skel["cart"]:
            errors.append("missing cart")
        if not order_skel["payment_provider"]:
            errors.append("missing payment_provider")
        elif pp_errors := self.get_payment_provider_by_name(order_skel["payment_provider"]).can_checkout(order_skel):
            errors.extend(pp_errors)

        # TODO: ...
        return errors

    def freeze_order(
        self,
        order_skel: "SkeletonInstance",
    ) -> "SkeletonInstance":
        # TODO:
        #  - recalculate cart
        #  - copy values (should not be hit by update relations)

        return order_skel

    def assign_uid(
        self,
        order_skel: "SkeletonInstance",
    ) -> "SkeletonInstance":
        order_skel["order_uid"] = "".join(
            f"-{c}" if i % 4 == 0 else c
            for i, c in enumerate(str(time.time()).replace(".", ""))
        ).strip("-")
        # TODO: customize by hook, claim in transaction, ...
        return order_skel

    @exposed
    # @force_post
    def checkout_order(
        self,
        order_key: db.Key,
    ):
        order_key = self.shop.api._normalize_external_key(order_key, "order_key")
        if not isinstance(order_key, db.Key):
            raise TypeError(f"order_key must be an instance of db.Key")
        skel = self.editSkel()
        if not skel.fromDB(order_key):
            raise core_errors.NotFound()

        if errors := self.can_order(skel):
            logging.error(errors)
            return JsonResponse({
                "errors": errors,
            }, status_code=400)
            raise e.InvalidStateError(", ".join(error_))

        skel["is_ordered"] = True
        # TODO: call hooks
        # TODO: charge order if it should directly be charged
        skel.toDB()
        return JsonResponse(skel)

    def can_order(
        self,
        order_skel: "SkeletonInstance",
    ) -> list["ErrorClassTBD"]:  # TODO
        errors = []
        if order_skel["is_ordered"]:
            errors.append("already is_ordered")
        if not order_skel["cart"]:
            errors.append("missing cart")
        if not order_skel["payment_provider"]:
            errors.append("missing payment_provider")
        elif pp_errors := self.get_payment_provider_by_name(order_skel["payment_provider"]).can_order(order_skel):
            errors.extend(pp_errors)

        # TODO: ...
        return errors

    # --- Internal helpers  ----------------------------------------------------
    def get_payment_provider_by_name(
        self,
        payment_provider_name: str,
    ) -> PaymentProviderAbstract:
        for pp in self.shop.payment_providers:
            if payment_provider_name == pp.name:
                return pp
        raise LookupError(f"Unknown payment provider {payment_provider_name}")
