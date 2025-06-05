from fastapi import APIRouter, Depends, HTTPException
from app.services.auth import get_current_user
from app.services.transaction import TransactionService
from app.services.cart import CartService
from datetime import datetime
from typing import List, Dict, Any

router = APIRouter()
transaction_service = TransactionService()
cart_service = CartService()

@router.post("/create")
async def create_transaction(
    transaction_data: dict,
    current_user: dict = Depends(get_current_user)
):
    try:
        # Add input validation with more detailed error messages
        if 'cart_items' not in transaction_data:
            raise HTTPException(
                status_code=400,
                detail="Missing cart_items in request"
            )

        # if not isinstance(transaction_data.get('cart_items'), list):
        #     raise HTTPException(
        #         status_code=400,
        #         detail="cart_items must be a list"
        #     )

        # if not transaction_data.get('cart_items'):
        #     raise HTTPException(
        #         status_code=400,
        #         detail="cart_items cannot be empty"
            # )

        if not transaction_data.get('razorpay_payment_id'):
            raise HTTPException(
                status_code=400,
                detail="razorpay_payment_id is required"
            )

        status = transaction_data.get('status')
        print(status)

        # Create the transaction using payment ID as order ID
        transaction = await transaction_service.create_transaction(
            cart_items=transaction_data['cart_items'],
            buyer_email=current_user['email'],
            razorpay_payment_id=transaction_data['razorpay_payment_id'],
            status = status
        )

        # Log successful transaction creation
        print(f"Transaction created successfully: {transaction.get('transaction_id')} with payment ID: {transaction_data['razorpay_payment_id']}")
        
        return transaction

    except HTTPException as he:
        # Re-raise HTTP exceptions
        raise he
    except Exception as e:
        print(f"Detailed transaction error: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create transaction: {str(e)}"
        )

@router.get("/get/{transaction_id}")
async def get_transaction(
    transaction_id: str,
    current_user: dict = Depends(get_current_user)
):
    try:
        # Get transaction from DynamoDB
        transaction = await transaction_service.get_transaction(
            transaction_id=transaction_id,
            buyer_email=current_user['email']
        )
        
        if not transaction:
            raise HTTPException(
                status_code=404,
                detail="Transaction not found"
            )
            
        return transaction
        
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch transaction: {str(e)}"
        ) 

@router.get("/history")
async def get_transactions(
    page: int = 1,
    limit: int = 10,
    current_user: dict = Depends(get_current_user)
):
    try:
        print(f"Fetching transactions for user: {current_user['email']}")  # Debug log
        
        result = await transaction_service.get_user_transactions(
            buyer_email=current_user['email'],
            page=page,
            limit=limit
        )
        
        print(f"Found {len(result['transactions'])} transactions")  # Debug log
        return result
        
    except Exception as e:
        print(f"Error in get_transactions: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch transactions: {str(e)}"
        ) 