from fastapi import APIRouter, Response, HTTPException, status, Header, Depends
from app.schemas.user import SignUpUserData, SignInUserData, GoogleCallbackRequest, VerifyPhoneRequest, ResendCodeRequest, CompleteGoogleSignupRequest, ForgotPasswordRequest, ResetPasswordRequest, VerifySellerData, UpdatePasswordRequest
import json
from app.services import auth as auth_service
import os
from typing import Optional
from app.services import dynamodb as dynamodb_service
from app.utils.referral import generate_referral_code
import uuid
from datetime import datetime
import names
import logging

router = APIRouter()
UserData = {}
logger = logging.getLogger(__name__)
USERS_TABLE = os.getenv('DYNAMODB_USER_TABLE')

@router.post("/signup")
async def signup(data: SignUpUserData):
    uname = names.get_full_name(gender=data.gender)
    try:
        global UserData
        user_item = {
            'username': {'S': uname},
            'fullname': {'S': data.fullname},
            'mobile': {'S': str(data.mobile)},
            'email': {'S': data.email},
            'gender': {'S': data.gender},
            'created_at': {'S': str(datetime.now())},
            'updated_at': {'S': str(datetime.now())},
            'isVerified': {'S': "UNVERIFIED"},
            'isDesigner': {'BOOL': False},
            'isRegistered': {'BOOL': False},
            'collections': {'NULL': True},
            'cart': {'NULL': True}
        }
        UserData = user_item
        response = await auth_service.user_sign_up(data)
        # print(response)
        dynamodb_service.put_item(table_name=USERS_TABLE, item=user_item)
        return Response(
            status_code=200, 
            content=json.dumps(response),
            media_type="application/json"
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    #     response = await auth_service.user_sign_up(data)
    #     return Response(
    #         status_code=200,
    #         content=json.dumps(response),
    #         media_type="application/json" 
    #     )
    # except Exception as e:
    #     raise HTTPException(status_code=400, detail=str(e))

@router.get("/google/auth")
async def google_auth():
    try:
        auth_url_response = await auth_service.get_google_auth_url()
        return auth_url_response
    except Exception as e:
        print(f"Error in google_auth route: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.post("/google/callback")
async def google_callback(request: GoogleCallbackRequest):
    try:
        result = await auth_service.handle_google_callback(request.code)
        return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.post("/verify-phone")
async def verify_phone(request: VerifyPhoneRequest):
    try:
        # First check if user already exists
        existing_user = dynamodb_service.get_item(
            table_name=USERS_TABLE,
            key={'email': {'S': request.email}}
        )
        
        if existing_user and existing_user.get('isRegistered', {}).get('BOOL', False):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User is already registered"
            )

        # If referral code is provided, validate it
        if request.referral_code:
            # Check if referral code exists using GSI
            referrer = dynamodb_service.query(
                table_name=USERS_TABLE,
                index_name='UserReferralGSI',
                key_condition_expression='#referral_code = :code',
                expression_attribute_names={
                    '#referral_code': 'referral_code'
                },
                expression_attribute_values={
                    ':code': {'S': request.referral_code}
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
            if referrer_email == request.email:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot use your own referral code"
                )

        # Now verify the SMS code
        verification_result = await auth_service.verify_sms_code(
            phone_number=request.phone_number,
            verification_code=request.verification_code,
            email=request.email,
        )
        
        if verification_result['status'] == 'SUCCESS':
            # Prepare update expression for the new user
            update_expression = "SET #isRegistered = :reg, #updated_at = :time, #referral_score = :points, #referral_count = :one, #referral_milestone_reached = :false, #referral_code = :ref_code_new"
            expression_attribute_names = {
                '#isRegistered': 'isRegistered',
                '#updated_at': 'updated_at',
                '#referral_score': 'referral_score',
                '#referral_count': 'referral_count',
                '#referral_milestone_reached': 'referral_milestone_reached',
                '#referral_code': 'referral_code'
            }
            expression_values = {
                ':reg': {'BOOL': True},
                ':time': {'S': str(datetime.now())},
                ':points': {'N': '0'},
                ':one': {'N': '0'},
                ':false': {'BOOL': False},
               ':ref_code_new': {'S': generate_referral_code()}
            }
            
            # If referral code was provided and validated, add referee_code
            if request.referral_code:
                update_expression += ", #referee_code = :ref_code"
                expression_attribute_names['#referee_code'] = 'referee_code'
                expression_values[':ref_code'] = {'S': request.referral_code}
                
                # Also initialize points for the new user
                # update_expression += ", #referral_score = :points"
                # expression_attribute_names['#referral_score'] = 'referral_score'
                # expression_values[':points'] = {'N': '0'}
                
                # # Update referrer's referred_users count
                # try:
                #     dynamodb_service.update_item(
                #         table_name='User',
                #         key={'email': {'S': referrer_email}},
                #         update_expression="SET #referral_count = :one",
                #         expression_attribute_names={
                #             '#referral_count': 'referral_count'
                #         },
                #         expression_attribute_values={
                #             ':one': {'N': '1'}
                #         }
                #     )
                #     logger.info(f"Updated referrer {referrer_email}'s referred_users count")
                # except Exception as e:
                #     logger.error(f"Error updating referrer count: {str(e)}")
            
            # Update the new user's record
            try:
                dynamodb_service.update_item(
                    table_name='User',
                    key={'email': {'S': request.email}},
                    update_expression=update_expression,
                    expression_attribute_names=expression_attribute_names,
                    expression_attribute_values=expression_values
                )
                logger.info(f"Successfully registered user {request.email}")
            except Exception as e:
                logger.error(f"Error updating user record: {str(e)}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to update user record"
                )

            return {
                "status": "SUCCESS",
                "message": "Phone verified and registration completed successfully",
                "used_referral": bool(request.referral_code)
            }
        
        return verification_result

    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Error in verify_phone: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

@router.post("/resend-code")
async def resend_code(request: ResendCodeRequest):
    try:
        result = await auth_service.resend_verification_code(
            phone_number=request.phone_number,
            email=request.email,
        )
        return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@router.post("/complete-google-signup")
async def complete_google_signup(request: CompleteGoogleSignupRequest):
    try:
        result = await auth_service.complete_google_signup(
            request.email,
            request.phone_number,
            request.google_tokens
        )
        return result
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@router.post("/signin")
async def signin(data: SignInUserData):
    try:
        response = await auth_service.user_sign_in(data)
        userdata = dynamodb_service.get_item(
            table_name=USERS_TABLE, 
            key={'email': {'S': data.email}},
        )

        fullresponse = {
            "response": response,
            "userdata": userdata
        }

        if userdata and response:
            return Response(
                status_code=200,
                content=json.dumps(fullresponse),
                media_type="application/json"
            )
        else:
            raise HTTPException(status_code=400, detail="User not found")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/signout")
async def signout(authorization: Optional[str] = Header(None)):
    try:
        if not authorization or not authorization.startswith("Bearer "):
            # If no token, just return success as we'll clear cookies anyway
            return Response(
                status_code=200,
                content=json.dumps({
                    "status": "SUCCESS",
                    "message": "Session cleared"
                }),
                media_type="application/json"
            )
            
        access_token = authorization.split(" ")[1]
        response = await auth_service.user_sign_out(access_token)

        return Response(
            status_code=200,
            content=json.dumps(response),
            media_type="application/json"
        )
    except Exception as e:
        # Even if backend sign out fails, return success as we'll clear cookies on frontend
        return Response(
            status_code=200,
            content=json.dumps({
                "status": "SUCCESS",
                "message": "Session cleared"
            }),
            media_type="application/json"
        )

@router.post("/forgot-password")
async def forgot_password(request: ForgotPasswordRequest):
    try:
        result = await auth_service.initiate_forgot_password(request.email)
        return Response(
            status_code=200,
            content=json.dumps(result),
            media_type="application/json"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@router.post("/reset-password")
async def reset_password(request: ResetPasswordRequest):
    try:
        result = await auth_service.confirm_forgot_password(
            email=request.email,
            code=request.code,
            new_password=request.new_password
        )
        return Response(
            status_code=200,
            content=json.dumps(result),
            media_type="application/json"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@router.put("/update-password")
async def update_password(
    data: UpdatePasswordRequest,
    current_user: dict = Depends(auth_service.get_current_user)
):
    try:
        result = await auth_service.change_user_password(
            email=current_user['email'],
            current_password=data.currentPassword,
            new_password=data.newPassword
        )
        return Response(
            status_code=200,
            content=json.dumps(result),
            media_type="application/json"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

