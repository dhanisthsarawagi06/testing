from fastapi import APIRouter, Depends, Response, HTTPException
from app.services.auth import get_current_user
from app.services.cart import CartService

router = APIRouter()
cart_service = CartService()

@router.get("")
async def get_cart(
    response: Response,
    current_user: dict = Depends(get_current_user)
):
    try:
        cart = await cart_service.get_cart(current_user['email'])
        # Convert DynamoDB format to regular JSON
        cart_items = []
        for item in cart:
            if 'M' in item:
                cart_items.append({
                    'id': item['M']['id']['S'],
                    'title': item['M']['title']['S'],
                    'price': float(item['M']['price']['N']),
                    'thumbnail_url': item['M']['thumbnail_url']['S']
                })
        return {"cart": cart_items}
    except Exception as e:
        print(f"Error fetching cart: {str(e)}")
        return {"cart": []}

@router.post("")
async def update_cart(
    cart_data: dict,
    current_user: dict = Depends(get_current_user)
):
    try:
        # Validate cart data
        cart_items = cart_data.get('cart', [])
        for item in cart_items:
            if not all(key in item for key in ['id', 'title', 'price', 'thumbnail_url']):
                raise HTTPException(
                    status_code=400,
                    detail="Invalid cart item format"
                )
        
        success = await cart_service.update_cart(
            current_user['email'], 
            cart_items
        )
        if not success:
            raise HTTPException(
                status_code=500,
                detail="Failed to update cart"
            )
        return {"success": True}
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update cart: {str(e)}"
        ) 