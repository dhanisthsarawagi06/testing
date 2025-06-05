from fastapi import APIRouter, HTTPException, Depends
from app.services import dynamodb as dynamodb_service
from app.services.auth import get_current_user
from typing import Optional
import json
from app.services.payment import PaymentService
from datetime import datetime
import os

router = APIRouter()
payment_service = PaymentService(dynamodb_service._get_dynamodb_client())
USERS_TABLE = os.getenv('DYNAMODB_USER_TABLE')
DESIGN_TABLE = os.getenv('DYNAMODB_DESIGN_TABLE')

@router.get("/users")
async def get_all_users(current_user: dict = Depends(get_current_user)):
    try:
        # Check if user is admin
        user_data = dynamodb_service.get_item(
            table_name=USERS_TABLE,
            key={'email': {'S': current_user['email']}}
        )
        
        if not user_data.get('isAdmin', {}).get('BOOL', False):
            raise HTTPException(status_code=403, detail="Not authorized")        
        # Scan all users
        users = dynamodb_service.scan_table(USERS_TABLE)
        
        # Process user data
        processed_users = []
        for user in users:
            try:
                # Query designs using DesignSellerGSI
                designs = dynamodb_service.query_table(
                    table_name=DESIGN_TABLE,
                    index_name='DesignSellerGSI',
                    key_condition_expression='seller_email = :email',
                    expression_attribute_values={
                        ':email': {'S': user['email']['S']}
                    }
                )
                
                # Filter for verified designs with unpaid sales
                total_payment = 0
                total_credits = 0
                sold_designs = []
                
                for design in designs:
                    if design.get('verification_status', {}).get('S') == 'Verified':
                        unpaid_sales = payment_service.get_unpaid_sales(design)
                        if unpaid_sales > 0:
                            price = float(design.get('price', {}).get('N', 0))
                            payment_method = design.get('payment_method', {}).get('S', '')
                            
                            cash, credits = payment_service.calculate_payment_and_credits(
                                price * unpaid_sales,
                                payment_method
                            )
                            
                            total_payment += cash
                            total_credits += credits
                            sold_designs.append(design)
                
                if sold_designs:
                    processed_users.append({
                        'email': user.get('email', {}).get('S', ''),
                        'designs_sold': len(sold_designs),
                        'payment_due': total_payment,
                        'credits_due': total_credits,
                        'is_designer': user.get('isDesigner', {}).get('BOOL', False)
                    })
            except Exception as user_error:
                print(f"Error processing user {user.get('email', {}).get('S', '')}: {str(user_error)}")
                continue
            
        return processed_users

    except Exception as e:
        print(f"Admin route error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/user-designs/{email}")
async def get_user_designs(
    email: str,
    current_user: dict = Depends(get_current_user)
):
    try:
        # Check if user is admin
        user_data = dynamodb_service.get_item(
            table_name=USERS_TABLE,
            key={'email': {'S': current_user['email']}}
        )
        
        if not user_data.get('isAdmin', {}).get('BOOL', False):
            raise HTTPException(status_code=403, detail="Not authorized")

        # Query designs using DesignSellerGSI
        designs = dynamodb_service.query_table(
            table_name=DESIGN_TABLE,
            index_name='DesignSellerGSI',
            key_condition_expression='seller_email = :email',
            expression_attribute_values={
                ':email': {'S': email}
            }
        )
        
        # Process designs data - only include verified designs with sales
        processed_designs = []
        for design in designs:
            # Check if design is verified and has sales
            if (design.get('verification_status', {}).get('S') == 'Verified' and 
                int(design.get('total_sold', {}).get('N', '0')) > 0):
                
                processed_designs.append({
                    'design_name': design.get('title', {}).get('S', ''),
                    'category': design.get('category', {}).get('S', ''),
                    'price': float(design.get('price', {}).get('N', 0)),
                    'sold_count': int(design.get('total_sold', {}).get('N', 0)),
                    'unpaid_sales':int(payment_service.get_unpaid_sales(design)),
                    'rating': float(design.get('rating', {}).get('N', 0)),
                    'image_url': design.get('thumbnail_url', {}).get('S', ''),
                    'status': design.get('verification_status', {}).get('S', 'Pending'),
                    'payment_method': design.get('payment_method', {}).get('S', ''),
                })
            
        return processed_designs

    except Exception as e:
        print(f"Error fetching user designs: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/mark-paid/{email}")
async def mark_user_paid(
    email: str,
    payment_details: dict,
    current_user: dict = Depends(get_current_user),

):
    try:
        # Check if user is admin
        user_data = dynamodb_service.get_item(
            table_name=USERS_TABLE,
            key={'email': {'S': current_user['email']}}
        )
        
        if not user_data.get('isAdmin', {}).get('BOOL', False):
            raise HTTPException(status_code=403, detail="Not authorized")

        payment_service = PaymentService(dynamodb_service._get_dynamodb_client())
        payment_details['admin_email'] = current_user['email']
        success_db = payment_service.update_payment_details(email, payment_details)
        success = await payment_service.mark_designs_as_paid(email)
        
        if success_db and success:
            return {"message": "Successfully marked designs as paid"}
        else:
            raise HTTPException(status_code=500, detail="Failed to mark designs as paid")

    except Exception as e:
        print(f"Error marking designs as paid: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/payment-details/{email}")
async def get_payment_details(
    email: str,
    current_user: dict = Depends(get_current_user)
):
    try:
        # Check if user is admin
        user_data = dynamodb_service.get_item(
            table_name=USERS_TABLE,
            key={'email': {'S': current_user['email']}}
        )
        
        if not user_data.get('isAdmin', {}).get('BOOL', False):
            raise HTTPException(status_code=403, detail="Not authorized")

        # Get user details with UPI ID
        user = dynamodb_service.get_item_subset(
            table_name=USERS_TABLE,
            key={'email': {'S': email}},
            projection_expression="#SellerProfile.#upiId",
            expression_attribute_names={
                "#SellerProfile": "SellerProfile",
                "#upiId": "upiId"
            }
        )

        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        upi_id = user.get('SellerProfile', {}).get('M', {}).get('upiId', {}).get('S', '')
        
        return {
            "upiId": upi_id
        }

    except Exception as e:
        print(f"Error fetching payment details: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/pending-verification")
async def get_pending_verification(current_user: dict = Depends(get_current_user)):
    try:
        # Check if user is admin
        user_data = dynamodb_service.get_item(
            table_name=USERS_TABLE,
            key={'email': {'S': current_user['email']}}
        )
        
        if not user_data.get('isAdmin', {}).get('BOOL', False):
            raise HTTPException(status_code=403, detail="Not authorized")
        
        # Scan users and filter for pending verification
        users = dynamodb_service.scan_table(USERS_TABLE)
        pending_users = []
        
        for user in users:
            if user.get('isVerified', {}).get('S') == 'PENDING':
                seller_profile = user.get('SellerProfile', {}).get('M', {})
                pending_users.append({
                    'email': user.get('email', {}).get('S', ''),
                    'fullname': user.get('fullname', {}).get('S', ''),
                    'mobile_number': user.get('mobile', {}).get('S', ''),
                    'pan_number': seller_profile.get('panNumber', {}).get('S', ''),
                    'aadhar_number': seller_profile.get('aadharNumber', {}).get('S', ''),
                    'upi_id': seller_profile.get('upiId', {}).get('S', '')
                })
        
        return pending_users
        
    except Exception as e:
        print(f"Error fetching unverified users: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/verify-user/{email}")
async def verify_user(
    email: str,
    verification_status: str,
    verification_comments: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    try:
        # Check if user is admin
        user_data = dynamodb_service.get_item(
            table_name=USERS_TABLE,
            key={'email': {'S': current_user['email']}}
        )
        
        if not user_data.get('isAdmin', {}).get('BOOL', False):
            raise HTTPException(status_code=403, detail="Not authorized")

        # Update user verification status
        update_expr = "SET #verification_status = :status, #verified_by = :approver, #verified_at = :time"
        expr_attrs_names = {
            "#verification_status": "isVerified",
            "#verified_by": "verified_by",
            "#verified_at": "verified_at"
        }
        expr_attrs_values = {
            ":status": {"S": verification_status},
            ":approver": {"S": current_user['email']},
            ":time": {"S": str(datetime.now())}
        }

        if verification_comments:
            update_expr += ", #verification_comments = :comments"
            expr_attrs_names["#verification_comments"] = "verification_comments"
            expr_attrs_values[":comments"] = {"S": verification_comments}

        dynamodb_service.update_item(
            table_name=USERS_TABLE,
            key={"email": {"S": email}},
            update_expression=update_expr,
            expression_attribute_names=expr_attrs_names,
            expression_attribute_values=expr_attrs_values
        )

        return {"message": f"User verification status updated to {verification_status}"}

    except Exception as e:
        print(f"Error updating user verification: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/unverified-users")
async def get_unverified_users(current_user: dict = Depends(get_current_user)):
    try:
        # Check if user is admin
        user_data = dynamodb_service.get_item(
            table_name=USERS_TABLE,
            key={'email': {'S': current_user['email']}}
        )
        
        if not user_data.get('isAdmin', {}).get('BOOL', False):
            raise HTTPException(status_code=403, detail="Not authorized")
        
        # Scan users and filter for unverified/rejected users
        users = dynamodb_service.scan_table(USERS_TABLE)
        unverified_users = []
        
        for user in users:
            status = user.get('isVerified', {}).get('S', 'UNVERIFIED')
            if status in ['UNVERIFIED', 'REJECTED']:
                unverified_users.append({
                    'email': user.get('email', {}).get('S', ''),
                    'fullname': user.get('fullname', {}).get('S', ''),
                    'mobile': user.get('mobile', {}).get('S', ''),
                    'status': status,
                    'isDesigner': user.get('isDesigner', {}).get('BOOL', False),
                    'created_at': user.get('created_at', {}).get('S', '')
                })
        
        return unverified_users
        
    except Exception as e:
        print(f"Error fetching unverified users: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/mark-designer/{email}")
async def mark_as_designer(
    email: str,
    current_user: dict = Depends(get_current_user)
):
    try:
        # Check if user is admin
        user_data = dynamodb_service.get_item(
            table_name=USERS_TABLE,
            key={'email': {'S': current_user['email']}}
        )
        
        if not user_data.get('isAdmin', {}).get('BOOL', False):
            raise HTTPException(status_code=403, detail="Not authorized")

        # Update user as designer
        update_expr = "SET #isdesigner = :status"
        expr_attrs_names = {
            "#isdesigner": "isDesigner"
        }
        expr_attrs_values = {
            ":status": {"BOOL": True}
        }

        dynamodb_service.update_item(
            table_name=USERS_TABLE,      
            key={"email": {"S": email}},
            update_expression=update_expr,
            expression_attribute_names=expr_attrs_names,
            expression_attribute_values=expr_attrs_values
        )

        return {"message": "User marked as designer successfully"}

    except Exception as e:
        print(f"Error marking user as designer: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    
