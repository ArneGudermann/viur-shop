import enum


class ArticleAvailability(enum.Enum):
    IN_STOCK = "instock"
    OUT_OF_STOCK = "outofstock"
    LIMITED = "limited"
    DISCONTINUED = "discontinued"
    PREORDER = "preorder"



class CartType(enum.Enum):
    WISHLIST = "wishlist"
    BASKET = "basket"

