from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Form, Header, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse

from .. import templates
from ..store import PlacedItemTable, PlacementTable, Product, ProductTable
from ..store.product import ProductCompact

router = APIRouter()

# NOTE: Do NOT store this data in database (the data is transient and should be kept in memory)
# NOTE: Or should this be optionally stored in database?
order_sessions: dict[int, dict[UUID, Product]] = {}
last_session_id = 0


def create_new_session() -> int:
    global last_session_id
    last_session_id += 1
    new_session_id = last_session_id
    order_sessions[new_session_id] = {}
    return new_session_id


def compute_total_price(order_items: dict[UUID, Product]) -> str:
    total_price = 0
    for item in order_items.values():
        total_price += item.price
    return Product.to_price_str(total_price)


@router.post("/orders", response_class=Response)
async def create_new_order():
    location = f"/orders/{create_new_session()}"
    return Response(
        location,
        status_code=status.HTTP_201_CREATED,
        headers={"location": location, "hx-location": location},
    )


@router.get("/orders/{session_id}", response_class=HTMLResponse)
async def get_order_session(request: Request, session_id: int):
    if (order_items := order_sessions.get(session_id)) is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    total_price = compute_total_price(order_items)
    products = await ProductTable.select_all()
    return HTMLResponse(
        templates.orders(request, session_id, products, order_items, total_price)
    )


@router.get("/orders/{session_id}/confirm", response_class=HTMLResponse)
async def get_order_session_to_confirm(request: Request, session_id: int):
    if (order_items := order_sessions.get(session_id)) is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
    if len(order_items) == 0:
        total_price = Product.to_price_str(0)
        placement_status = "エラー：商品が選択されていません"
    else:
        placement_status = ""
    total_price = 0
    products: dict[int, ProductCompact] = {}
    for item in order_items.values():
        total_price += item.price
        if item.product_id in products:
            products[item.product_id].count += 1
        else:
            products[item.product_id] = ProductCompact(item.name, item.price)
    return HTMLResponse(
        templates.components.order_confirm(
            request,
            session_id,
            products,
            len(order_items),
            Product.to_price_str(total_price),
            placement_status,
        )
    )


@router.post("/orders/{session_id}", response_class=HTMLResponse)
async def place_order(request: Request, session_id: int):
    if (order_items := order_sessions.get(session_id)) is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    if len(order_items) == 0:
        total_price = Product.to_price_str(0)
        placement_status = "エラー：商品が選択されていません"
        order_frozen = False
    else:
        order_sessions.pop(session_id)

        total_price = compute_total_price(order_items)
        product_ids = [item.product_id for item in order_items.values()]
        placement_id = await PlacedItemTable.issue(product_ids)
        # TODO: add a branch for out of stock error
        await PlacementTable.insert(placement_id)
        placement_status = f"注文番号: #{placement_id}"
        order_frozen = True
    return HTMLResponse(
        templates.components.order_session(
            request,
            session_id,
            order_items,
            total_price,
            placement_status=placement_status,
            order_frozen=order_frozen,
        )
    )


@router.post("/orders/{session_id}/item")
async def add_order_item(
    request: Request,
    session_id: int,
    product_id: Annotated[int, Form()],
    hx_request: Annotated[str | None, Header()] = None,
) -> Response:
    if (product := await ProductTable.by_product_id(product_id)) is None:
        raise HTTPException(status_code=404, detail=f"Product {product_id} not found")
    if (order_items := order_sessions.get(session_id)) is None:
        # NOTE: the branching below is a bit complicated so it might be changed in the future
        if hx_request == "true":
            # If requested by `hx-post`, respond with a new order session even when the `session_id` is not valid
            new_session_id = create_new_session()
            await add_order_item(request, new_session_id, product_id)
            location = f"/orders/{new_session_id}"
            return Response(
                f"Session {session_id} not found; redirecting to a newly created order",
                status_code=status.HTTP_200_OK,
                headers={"location": location, "hx-redirect": location},
            )
        else:
            # otherwise report back that the `session_id` is not valid
            raise HTTPException(
                status_code=404, detail=f"Session {session_id} not found"
            )

    order_items[uuid4()] = product
    return HTMLResponse(
        templates.components.order_session(
            request, session_id, order_items, compute_total_price(order_items)
        )
    )


@router.delete("/orders/{session_id}/item/{index}", response_class=HTMLResponse)
async def delete_order_item(request: Request, session_id: int, index: UUID):
    if (order_items := order_sessions.get(session_id)) is None:
        raise HTTPException(status_code=404, detail=f"Session {session_id} not found")

    order_items.pop(index)
    return HTMLResponse(
        templates.components.order_session(
            request, session_id, order_items, compute_total_price(order_items)
        )
    )


@router.delete("/orders/{session_id}")
async def clear_order_items(
    request: Request,
    session_id: int,
    hx_request: Annotated[str | None, Header()] = None,
) -> Response:
    if (order_items := order_sessions.get(session_id)) is None:
        # NOTE: the branching below is a bit complicated so it might be changed in the future
        if hx_request == "true":
            # If requested by `hx-post`, respond with a new order session even when the `session_id` is not valid
            location = f"/orders/{create_new_session()}"
            return Response(
                f"Session {session_id} not found; redirecting to a newly created order",
                status_code=status.HTTP_200_OK,
                headers={"location": location, "hx-redirect": location},
            )
        else:
            # otherwise report back that the `session_id` is not valid
            raise HTTPException(
                status_code=404, detail=f"Session {session_id} not found"
            )

    order_items.clear()

    return HTMLResponse(
        templates.components.order_session(
            request, session_id, {}, Product.to_price_str(0)
        )
    )


# TODO: add proper path operation for order deferral
# # TODO: Store this data in database
# deferred_order_lists: dict[int, list[Product | None]] = {}
#
#
# @router.post("/deferred_orders/{session_id}", response_class=HTMLResponse)
# async def post_order_defer_session(request: Request, session_id: int):
#     if (order_items := order_sessions.get(session_id)) is None:
#         raise HTTPException(status_code=404, detail=f"Session {session_id} not found")
#     if deferred_order_lists.get(session_id):
#         raise HTTPException(
#             status_code=status.HTTP_409_CONFLICT,
#             detail=f"Deferred session {session_id} already exists",
#         )
#     order_sessions.pop(session_id)
#     deferred_order_lists[session_id] = order_items
#     # TODO: respond with a message about the success of the deferral action
#     # placement_status = "注文を保留しました"
#     # return HTMLResponse(
#     #     templates.components.order_session(request, session_id, [], Product.to_price_str(0), placement_status=placement_status)
#     # )
