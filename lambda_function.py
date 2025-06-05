from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum
from pydantic import BaseModel, EmailStr
import boto3
import os
import logging
import hmac
import base64
import hashlib

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize Cognito client
cognito = boto3.client('cognito-idp',
    region_name=os.getenv('AWS_REGION')
)

def get_secret_hash(username):
    msg = username + os.getenv('COGNITO_CLIENT_ID')
    dig = hmac.new(
        str(os.getenv('COGNITO_CLIENT_SECRET')).encode('utf-8'), 
        msg=msg.encode('utf-8'),
        digestmod=hashlib.sha256
    ).digest()
    return base64.b64encode(dig).decode()

class SignUpRequest(BaseModel):
    email: EmailStr
    password: str

class SignInRequest(BaseModel):
    email: EmailStr
    password: str

@app.post("/signup")
async def signup(data: SignUpRequest):
    logger.info("Signup endpoint called")
    try:
        client_id = os.getenv('COGNITO_CLIENT_ID')
        logger.info(f"Using Client ID: {client_id}")
        
        if not client_id:
            raise HTTPException(status_code=500, detail="COGNITO_CLIENT_ID not configured")

        # Get secret hash
        secret_hash = get_secret_hash(data.email)
            
        response = cognito.sign_up(
            ClientId=client_id,
            Username=data.email,
            Password=data.password,
            SecretHash=secret_hash,
            UserAttributes=[
                {'Name': 'email', 'Value': data.email},
            ]
        )
        logger.info(f"Signup successful: {response}")
        return {
            "status": "SUCCESS",
            "message": "User registered successfully",
            "user_sub": response['UserSub']
        }
    except Exception as e:
        logger.error(f"Signup error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/signin")
async def signin(data: SignInRequest):
    logger.info("Signin endpoint called")
    try:
        client_id = os.getenv('COGNITO_CLIENT_ID')
        user_pool_id = os.getenv('USER_POOL_ID')
        logger.info(f"Using Client ID for signin: {client_id}")
        logger.info(f"Using User Pool ID: {user_pool_id}")

        if not client_id or not user_pool_id:
            raise HTTPException(
                status_code=500, 
                detail="COGNITO_CLIENT_ID or COGNITO_USER_POOL_ID not configured"
            )

        # Get secret hash
        secret_hash = get_secret_hash(data.email)

        # Initiate auth
        auth_response = cognito.initiate_auth(
            ClientId=client_id,
            AuthFlow='USER_PASSWORD_AUTH',
            AuthParameters={
                'USERNAME': data.email,
                'PASSWORD': data.password,
                'SECRET_HASH': secret_hash
            }
        )
        
        auth_result = auth_response.get('AuthenticationResult', {})
        logger.info("Authentication successful, fetching user details")
        
        # Get user details using admin_get_user
        try:
            user_details = cognito.admin_get_user(
                UserPoolId=user_pool_id,
                Username=data.email
            )
            logger.info(f"Successfully retrieved user details: {user_details}")
            
            # Extract user attributes
            user_attrs = {}
            for attr in user_details.get('UserAttributes', []):
                user_attrs[attr['Name']] = attr['Value']
                        
            return {
                "status": "SUCCESS",
                "message": "Successfully signed in",
                "tokens": {
                    "access_token": auth_result.get('AccessToken'),
                    "id_token": auth_result.get('IdToken'),
                    "refresh_token": auth_result.get('RefreshToken'),
                    "expires_in": auth_result.get('ExpiresIn')
                },
                "user": {
                    "email": user_attrs.get('email'),
                    "email_verified": user_attrs.get('email_verified'),
                    "sub": user_attrs.get('sub'),
                    "user_status": user_details.get('UserStatus'),
                    "created_date": user_details.get('UserCreateDate'),
                    "last_modified_date": user_details.get('UserLastModifiedDate')
                }
            }
            
        except Exception as admin_error:
            logger.error(f"Error getting user details: {str(admin_error)}")
            # If admin_get_user fails, still return the tokens
            return {
                "status": "SUCCESS",
                "message": "Successfully signed in (user details unavailable)",
                "tokens": {
                    "access_token": auth_result.get('AccessToken'),
                    "id_token": auth_result.get('IdToken'),
                    "refresh_token": auth_result.get('RefreshToken'),
                    "expires_in": auth_result.get('ExpiresIn')
                }
            }
            
    except cognito.exceptions.NotAuthorizedException:
        logger.error("Invalid credentials")
        raise HTTPException(status_code=401, detail="Invalid email or password")
    except cognito.exceptions.UserNotConfirmedException:
        logger.error("User not confirmed")
        raise HTTPException(status_code=400, detail="Please verify your email first")
    except Exception as e:
        logger.error(f"Signin error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

# Mangum handler
handler = Mangum(app)

def lambda_handler(event, context):
    logger.info(f"Received event: {event}")
    return handler(event, context)