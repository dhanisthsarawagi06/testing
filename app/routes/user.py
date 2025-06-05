from fastapi import APIRouter, HTTPException, Header, Depends, Request, status
from app.schemas.user import VerifySellerData, UpdateUserData, UpdateUserUpiId
import app.services.dynamodb as dynamodb_service
from app.services import auth as auth_service
from fastapi import HTTPException
from typing import Optional, List, Union
from datetime import datetime
from app.services.auth import get_current_user
import logging
from app.services.auth import get_current_user
import json
from app.utils.referral import generate_referral_code
import boto3
from boto3.dynamodb.conditions import Key
import os

router = APIRouter()

USERS_TABLE = os.getenv('DYNAMODB_USER_TABLE')
DESIGN_TABLE = os.getenv('DYNAMODB_DESIGN_TABLE')
TRANSACTION_TABLE = os.getenv('DYNAMODB_TRANSACTION_TABLE')
PAYMENT_HISTORY_TABLE = os.getenv('DYNAMODB_PAYMENT_HISTORY_TABLE')

@router.post("/verify")
async def verify(data: VerifySellerData, current_user: dict = Depends(get_current_user)):
    try:
        email = current_user.get('email')
        if not email:
            raise HTTPException(status_code=400, detail="User not found")

        dynamodb_service.update_item(
            table_name=USERS_TABLE,
            key={"email": {"S": email}},
            update_expression="SET #isVerified = :isVerified, #SellerProfile = :SellerProfile",
            expression_attribute_names={"#isVerified": "isVerified", "#SellerProfile": "SellerProfile"}, 
            expression_attribute_values={":isVerified": {"S": "PENDING"}, ":SellerProfile": {"M": {"panNumber": {"S": data.panNumber}, "aadharNumber": {"S": data.aadharNumber}, "upiId": {"S": data.upiId}}}}
        )
    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        return {"message": "Seller verified successfully"}

@router.get("/details")
async def get_user_details(current_user: dict = Depends(get_current_user)):
    try:
        email = current_user.get('email')
        if not email:
            raise HTTPException(status_code=400, detail="Email not found in token")

        user = dynamodb_service.get_item_subset(
            table_name=USERS_TABLE,
            key={'email': {'S': email}},
            projection_expression="#email, #username, #fullname, #isDesigner, #SellerProfile.#upiId",
            expression_attribute_names={"#email": "email","#isDesigner": "isDesigner", "#username": "username", "#fullname": "fullname", "#SellerProfile": "SellerProfile","#upiId":"upiId"}
        )

        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        return {
            "user": {
                "email": user.get('email', {}).get('S'),
                "username": user.get('username', {}).get('S'),
                "name": user.get('fullname', {}).get('S'),
                "upiId": user.get('SellerProfile', {}).get('M', {}).get('upiId', {}).get('S'),
                "isDesigner": user.get('isDesigner', {}).get('BOOL', False)
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@router.put("/update")
async def update_user(data:Union[UpdateUserData, UpdateUserUpiId], current_user: dict = Depends(get_current_user)):
    try:
        email = current_user.get('email')
        if not email:
            raise HTTPException(status_code=400, detail="Email not found in token")
        
        if isinstance(data, UpdateUserData):
            response = dynamodb_service.update_item(
                table_name=USERS_TABLE,
                key={'email': {'S': email}},
                update_expression="SET #fullname = :fullname, #updated_at = :updated_at",
                expression_attribute_names={"#fullname": "fullname", "#updated_at": "updated_at"},
                expression_attribute_values={":fullname":{"S": data.name}, ":updated_at":{"S": str(datetime.now())}}
            )
            return {
                    "message": "Details updated successfully",
                    "fullname": response.get('Attributes').get('fullname').get('S')
                }
        
        elif isinstance(data, UpdateUserUpiId):
            response = dynamodb_service.update_item(
                table_name=USERS_TABLE,
                key={'email': {'S': email}},
                update_expression="SET #SellerProfile.#upiId = :upiId",
                expression_attribute_names={"#SellerProfile": "SellerProfile", "#upiId": "upiId"},
                expression_attribute_values={":upiId":{"S": data.upiId}}
            )
            return {
                "message": "Payment detail updated successfully",
                "upiId": response.get('Attributes').get('SellerProfile').get('M').get('upiId').get('S')
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/delete")
async def delete_user(current_user: dict = Depends(get_current_user)):
    try:
        email = current_user.get('email')
        if not email:
            raise HTTPException(status_code=400, detail="Email not found in token")

        # Delete from Cognito
        await auth_service.delete_user(email)

        # Delete from DynamoDB
        dynamodb_service.delete_item(
            table_name=USERS_TABLE,
            key={'email': {'S': email}}
        )
        
        return {"message": "User deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/check-status")
async def check_user_status(request: Request, current_user: dict = Depends(get_current_user)):
    try:
        # Get user email from the validated token
        email = current_user.get('email')
        if not email:
            raise HTTPException(status_code=400, detail="Email not found in token")

        # Query DynamoDB for user status
        user = dynamodb_service.get_item(
            table_name=USERS_TABLE,
            key={'email': {'S': email}},
            # attributes_to_get=['isVerified', 'isDesigner']
        )

        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        response_data = {
            "isVerified": user.get('isVerified', {}).get('S', 'UNVERIFIED'),
            "isDesigner": user.get('isDesigner', {}).get('BOOL', False),
            "isAdmin": user.get('isAdmin', {}).get('BOOL', False)
        }
        return response_data

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
        
@router.get("/designs")
async def get_user_designs(
    page_posted: int = 1,
    page_purchased: int = 1,
    limit: int = 20,
    current_user: dict = Depends(get_current_user),
    dynamodb = Depends(dynamodb_service._get_dynamodb_client)
):
    """Get both posted and purchased designs for a user"""
    try:
        # Get posted designs using DesignSellerGSI
        posted_response = dynamodb.query(
            TableName=DESIGN_TABLE, 
            IndexName='DesignSellerGSI',
            KeyConditionExpression='seller_email = :email',
            ExpressionAttributeValues={
                ':email': {'S': current_user['email']}
            },
            Limit=limit,
            ScanIndexForward=False  # Latest first
        )

        # Get purchased designs using transaction table
        purchased_response = dynamodb.query(
            TableName=TRANSACTION_TABLE,
            IndexName='buyer_email-created_at-index',
            KeyConditionExpression='buyer_email = :email',
            ExpressionAttributeValues={
                ':email': {'S': current_user['email']},
                ':status': {'S': 'COMPLETED'}
            },
            FilterExpression='#status = :status',
            ExpressionAttributeNames={
                '#status': 'status'
            },
            Limit=limit,
            ScanIndexForward=False
        )

        # Process posted designs
        posted_designs = []
        for item in posted_response.get('Items', []):
            try:
                metadata = json.loads(item.get('metadata', {}).get('S', '{}'))
                file_type = metadata.get('fileType', '').upper()
                dimensions = metadata.get('dimensions', {})
                resolution = f"{dimensions.get('width', '')}x{dimensions.get('height', '')}"
            except (json.JSONDecodeError, AttributeError):
                file_type = ''
                resolution = ''
            
            posted_designs.append({
                'id': item['design_id']['S'],
                'title': item['title']['S'],
                'thumbnail_url': item['thumbnail_url']['S'],
                'price': float(item['price']['N']),
                'category': item['category']['S'],
                'status': item['verification_status']['S'],
                'created_at': item.get('created_at', {}).get('S'),
                'file_type': file_type,
                'resolution': resolution,
                'is_color_matching': item.get('is_color_matching', {}).get('BOOL', False),
                'color_matching_design_id': item.get('color_matching_design_id', {}).get('S', ''),
                'bundle_discount': item.get('bundle_discount', {}).get('N', '0')
            })

        # Process purchased designs (extract unique designs from transactions)
        purchased_designs = []
        seen_designs = set()
        for transaction in purchased_response.get('Items', []):
            for design in transaction['designs']['L']:
                design_id = design['M']['design_id']['S']
                if design_id not in seen_designs:
                    seen_designs.add(design_id)
                    purchased_designs.append({
                        'id': design_id,
                        'title': design['M']['title']['S'],
                        'thumbnail_url': design['M']['thumbnail_url']['S'],
                        'price': float(design['M']['price']['N']),
                        'purchased_at': transaction['created_at']['S'],
                        'is_color_matching': design['M']['is_color_matching']['BOOL']  ,
                        'color_matching_design_id': design['M']['color_matching_design_id']['S']
                    })

        return {
            "posted": {
                "designs": posted_designs,
                "has_more": 'LastEvaluatedKey' in posted_response
            },
            "purchased": {
                "designs": purchased_designs,
                "has_more": 'LastEvaluatedKey' in purchased_response
            }
        }

    except Exception as e:
        print(f"Error fetching user designs: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard")
async def get_user_designs(current_user: dict = Depends(get_current_user)):
    # try:
        email = current_user.get('email')
        if not email:
            raise HTTPException(status_code=400, detail="Email not found in token")

        # Query designs table using email as the key
        response = dynamodb_service.query(
            table_name=DESIGN_TABLE,
            index_name='DesignSellerGSI',
            key_condition_expression='#user_email = :email',
            expression_attribute_names={
                '#user_email': 'seller_email'
            },
            expression_attribute_values={
                ':email': {'S': email}
            },
            scan_index_forward=False  # This will return items in descending order (newest first)
        )
        # if 'Items' not in response:
        #     return {"designs": []}

        # Transform DynamoDB items into a more usable format
        designs = []
        for item in response:
            design = {
                # 'id': item.get('id', {}).get('S'),
                'title': item.get('title', {}).get('S'),
                'thumbnail_url': item.get('thumbnail_url', {}).get('S'),
                'created_at': item.get('created_at', {}).get('S'),
                'status': item.get('verification_status', {}).get('S', 'Pending'),
                'admin_comments': item.get('verification_comments', {}).get('S', 'No Comments'),
                'category': item.get('category', {}).get('S', ''),
                'price': float(item.get('price', {}).get('N', '0'))
            }
            designs.append(design)

        return {"designs": designs}

    # except Exception as e:
    #     raise HTTPException(status_code=500, detail="Failed to fetch designs")

@router.get("/payment-history")
async def get_payment_history(current_user: dict = Depends(get_current_user), dynamodb = Depends(dynamodb_service._get_dynamodb_client)):
    """Get payment history for a user"""
    try:
        email = current_user.get('email')
        if not email:
            raise HTTPException(status_code=400, detail="Email not found in token")

        # Query PaymentHistory using both partition key and sort key
        response = dynamodb.query(
            TableName=PAYMENT_HISTORY_TABLE,
            KeyConditionExpression='seller_email = :email',
            ExpressionAttributeValues={
                ':email': {'S': email}
            },
            ProjectionExpression="#pid, #pdate, #amount, #credits, #designs, #tid, #notes",
            ExpressionAttributeNames={
                "#pid": "payment_id",
                "#pdate": "payment_date",
                "#amount": "total_amount",
                "#credits": "total_credits",
                "#designs": "paid_designs",
                "#tid": "transaction_id",
                "#notes": "notes"
            }
        )

        if not response['Items']:
            return {
                "payments": [],
                "total_count": 0
            }

        # Transform DynamoDB items into a more usable format
        payments = []
        for item in response['Items']:
            payment = {
                'payment_id': item.get('payment_id', {}).get('S'),
                'payment_date': item.get('payment_date', {}).get('S'),
                'total_amount': float(item.get('total_amount', {}).get('N', '0')),
                'total_credits': int(item.get('total_credits', {}).get('N', '0')),
                'transaction_id': item.get('transaction_id', {}).get('S', ''),
                'notes': item.get('notes', {}).get('S', ''),
                'paid_designs': [
                    {
                        'title': design.get('M', {}).get('title', {}).get('S'),
                        'sales_count': int(design.get('M', {}).get('sales_count', {}).get('N', '0')),
                        'price': float(design.get('M', {}).get('price', {}).get('N', '0')),
                        'image_url': design.get('M', {}).get('image_url', {}).get('S'),
                        'payment_method': design.get('M', {}).get('payment_method', {}).get('S')
                    }
                    for design in item.get('paid_designs', {}).get('L', [])
                ]
            }
            payments.append(payment)

        return {
            "payments": payments,
            "total_count": len(payments)
        }

    except Exception as e:
        print(f"Error fetching payment history: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/referral")
async def get_referral_details(current_user: dict = Depends(get_current_user)):
    try:
        email = current_user.get('email')
        if not email:
            raise HTTPException(status_code=400, detail="Email not found in token")

        # Get user details including points and referral code
        user = dynamodb_service.get_item(
            table_name=USERS_TABLE,
            key={'email': {'S': email}}
        )

        # Count approved designs
        # Count approved designs
        designs = dynamodb_service.query(
            table_name=DESIGN_TABLE,
            index_name='DesignSellerGSI',
            key_condition_expression='#user_email = :email',
            filter_expression='#verification_status = :verification_status',
            expression_attribute_names={
                '#user_email': 'seller_email',
                '#verification_status': 'verification_status'
            },
            expression_attribute_values={
                ':email': {'S': email},
                ':verification_status': {'S': 'Verified'}
            }
        )

        approved_designs_count = len(designs)

        return {
            "score": int(user.get('referral_score', {'N': '0'}).get('N', '0')),
            "referral_code": user.get('referral_code', {'S': None}).get('S'),
            "referral_count": int(user.get('referral_count', {'N': '0'}).get('N', '0')),
            "approved_designs": approved_designs_count,
            "designs_needed": max(0, 10 - approved_designs_count)
        }
       
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/check-referral-milestone")
async def check_referral_milestone(current_user: dict = Depends(get_current_user)):
    try:
        user_email = current_user['email']
        # Get user's approved designs count
        designs = dynamodb_service.query(
            table_name=DESIGN_TABLE,
            index_name='DesignSellerGSI',
            key_condition_expression='#user_email = :email',
            filter_expression='#verification_status = :verification_status',
            expression_attribute_names={
                '#user_email': 'seller_email',
                '#verification_status': 'verification_status'
            },
            expression_attribute_values={
                ':email': {'S': user_email},
                ':verification_status': {'S': 'Verified'}
            }
        )
        approved_count = len(designs)
        
        # Get user details to check if they were referred
        user = dynamodb_service.get_item(
                table_name='User',
                key={'email': {'S': user_email}}
            )
        # Always get the referral code from the user record
        referral_code = user.get('referral_code', {}).get('S', None)
        # If not present, generate and store it
        if not referral_code:
            referral_code = generate_referral_code()
            dynamodb_service.update_item(
                table_name=USERS_TABLE,
                key={'email': {'S': user_email}},
                update_expression='SET #referral_code = :code',
                expression_attribute_names={
                    '#referral_code': 'referral_code'
                },
                expression_attribute_values={
                    ':code': {'S': referral_code}
                }
            )
        
        if approved_count >= 10:
           
            # Then check if user has a referee_code and hasn't triggered the milestone yet
            if user.get('referee_code') and not user.get('referral_milestone_reached', {}).get('BOOL', False):
                # Get referrer details using the referee_code
                referrer = dynamodb_service.query(
                    table_name=USERS_TABLE,
                    index_name='UserReferralGSI',
                    key_condition_expression='#referral_code = :code',
                    expression_attribute_names={
                        '#referral_code': 'referral_code'
                    },
                    expression_attribute_values={
                        ':code': {'S': user.get('referee_code', {}).get('S', '')}
                    }
                )

                if len(referrer)>0:
                    referrer_data = referrer[0]
                    referrer_email = referrer_data.get('email', {}).get('S')
                    
                    try:
                        # Update referrer's points and referral count
                        dynamodb_service.update_item(
                            table_name=USERS_TABLE,
                            key={'email': {'S': referrer_email}},
                            update_expression="ADD #referral_score :points, #referral_count :one",
                            expression_attribute_names={
                                '#referral_score': 'referral_score',
                                '#referral_count': 'referral_count'
                            },
                            expression_attribute_values={
                                ':points': {'N': '20'},
                                ':one': {'N': '1'}
                            }
                        )

                        # Update referred user's points and mark milestone as reached
                        dynamodb_service.update_item(
                            table_name='User',
                            key={'email': {'S': user_email}},
                            update_expression="ADD #referral_score :points SET #referral_milestone_reached = :true",
                            expression_attribute_names={
                                '#referral_score': 'referral_score',
                                '#referral_milestone_reached': 'referral_milestone_reached'
                            },
                            expression_attribute_values={
                                ':points': {'N': '10'},
                                ':true': {'BOOL': True}
                            }
                        )

                        return {
                            "status": "SUCCESS",
                            "message": "Referral milestone reached! Points awarded to both users.",
                            "referrer_points_added": 20,
                            "user_points_added": 10,
                            "referral_code": referral_code
                        }
                    except Exception as e:
                        raise HTTPException(
                            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail="Failed to update points and referral count"
                        )

            # return {
            #     "status": "SUCCESS",
            #     "message": "Referral code generated and/or milestone checked",
            #     "referral_code_generated": not user.get('referral_code'),
            #     "score": int(user.get('referral_score', {'N': '0'}).get('N', '0')),
            #     "referral_code": user.get('referral_code', {'S': None}).get('S'),
            #     "referral_count": int(user.get('referral_count', {'N': '0'}).get('N', '0')),
            #     "approved_designs": approved_count,
            #     "designs_needed": max(0, 10 - approved_count)
            # }
        
        return {
            "status": "SUCCESS",
            "message": "Not enough approved designs yet",
            "score": int(user.get('referral_score', {'N': '0'}).get('N', '0')),
            "referral_count": int(user.get('referral_count', {'N': '0'}).get('N', '0')),
            "approved_designs": approved_count,
            "designs_needed": max(0, 10 - approved_count),
            "referral_code": referral_code
        }
        
    except Exception as e:
        print(e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.get("/leaderboard")
async def get_leaderboard():
    try:
        # Scan User table for points
        response = dynamodb_service.scan(
            table_name='User',
            projection_expression='username, referral_score, referral_code, isAdmin',
             filter_expression='attribute_exists(referral_score)'
            
        )
        non_admin_users = [user for user in response if not user.get('isAdmin', {}).get('BOOL', False)]
        users = sorted(
            non_admin_users,
            key=lambda x: int(x.get('referral_score', {'N': '0'}).get('N', '0')),
            reverse=True
        )[:3]


        return {
            "leaderboard": [
                {
                    "username": user.get('username', {'S': 'Anonymous'}).get('S'),
                    "points": int(user.get('referral_score', {'N': '0'}).get('N')),
                    "has_referral": 'referral_code' in user
                }
                for user in users
            ]
        }
    except Exception as e:
        print(f"Error fetching leaderboard: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/verify-referral/{referral_code}")
async def verify_referral(referral_code: str, current_user: dict = Depends(get_current_user)):
    try:
        user_email = current_user['email']
        referrer = dynamodb_service.query(
                    table_name=USERS_TABLE,
                    index_name='UserReferralGSI',
                    key_condition_expression='#referral_code = :code',
                    expression_attribute_names={
                        '#referral_code': 'referral_code'
                    },
                    expression_attribute_values={
                        ':code': {'S': referral_code}
                    }
                )
        if len(referrer)>0:
                referrer_data = referrer[0]
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Referral Code"
            )
        
        referrer_email = referrer_data.get('email', {}).get('S')
        
        # Check if referrer is trying to use their own code
        if referrer_email == user_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot use your own referral code"
            )
        else:
            return {
                "status": "SUCCESS",
                "message": "Referral code verified",
                "referrer_email": referrer_email
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))