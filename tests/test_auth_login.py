import pytest
import boto3
import os
from moto import mock_cognitoidp
from app.services.auth import (
    initiate_phone_login,
    verify_phone_login,
    handle_google_login,
    user_sign_up,
    verify_sms_code,
    format_phone_number
)
from unittest.mock import patch, MagicMock
import json
import base64

# Test data
TEST_PHONE = "+919876543210"
TEST_EMAIL = "test@example.com"
TEST_PASSWORD = "TestPass123!"
TEST_VERIFICATION_CODE = "123456"
TEST_GOOGLE_CODE = "google_auth_code"

@pytest.fixture(scope='function')
def aws_credentials():
    """Mocked AWS Credentials for moto."""
    os.environ['AWS_ACCESS_KEY_ID'] = 'testing'
    os.environ['AWS_SECRET_ACCESS_KEY'] = 'testing'
    os.environ['AWS_SECURITY_TOKEN'] = 'testing'
    os.environ['AWS_SESSION_TOKEN'] = 'testing'
    os.environ['AWS_DEFAULT_REGION'] = 'us-east-1'
    os.environ['COGNITO_USER_POOL_ID'] = 'us-east-1_testing'
    os.environ['COGNITO_CLIENT_ID'] = 'test_client_id'
    os.environ['COGNITO_CLIENT_SECRET'] = 'test_client_secret'
    os.environ['COGNITO_DOMAIN_PREFIX'] = 'test-domain'
    os.environ['GOOGLE_REDIRECT_URI'] = 'http://localhost:3000/api/auth/callback'

@pytest.fixture(scope='function')
def cognito(aws_credentials):
    """Create mocked Cognito client."""
    with mock_cognitoidp():
        cognito_client = boto3.client('cognito-idp', region_name='us-east-1')
        
        # Create user pool
        user_pool = cognito_client.create_user_pool(
            PoolName='test_pool',
            Schema=[
                {
                    'Name': 'email',
                    'AttributeDataType': 'String',
                    'Required': True,
                    'Mutable': True
                },
                {
                    'Name': 'phone_number',
                    'AttributeDataType': 'String',
                    'Required': True,
                    'Mutable': True
                }
            ],
            AutoVerifiedAttributes=['phone_number']
        )
        
        # Create user pool client
        client = cognito_client.create_user_pool_client(
            UserPoolId=user_pool['UserPool']['Id'],
            ClientName='test_client',
            GenerateSecret=True,
            ExplicitAuthFlows=['ADMIN_NO_SRP_AUTH']
        )
        
        os.environ['COGNITO_USER_POOL_ID'] = user_pool['UserPool']['Id']
        os.environ['COGNITO_CLIENT_ID'] = client['UserPoolClient']['ClientId']
        
        yield cognito_client

async def create_verified_user(cognito):
    """Helper function to create and verify a test user"""
    # Create user
    signup_data = MagicMock(
        mobile=TEST_PHONE,
        email=TEST_EMAIL,
        password=TEST_PASSWORD
    )
    await user_sign_up(signup_data)
    
    # Verify phone
    await verify_sms_code(TEST_PHONE, TEST_VERIFICATION_CODE)
    
    return signup_data

@pytest.mark.asyncio
async def test_initiate_phone_login_success(cognito):
    """Test successful phone login initiation"""
    # Create and verify a user first
    await create_verified_user(cognito)
    
    # Test login initiation
    result = await initiate_phone_login(TEST_PHONE)
    assert result['status'] == 'OTP_SENT'
    assert result['phone_number'] == format_phone_number(TEST_PHONE)

@pytest.mark.asyncio
async def test_initiate_phone_login_unverified(cognito):
    """Test login initiation with unverified phone"""
    # Create user without verification
    signup_data = MagicMock(
        mobile=TEST_PHONE,
        email=TEST_EMAIL,
        password=TEST_PASSWORD
    )
    await user_sign_up(signup_data)
    
    # Test login initiation
    result = await initiate_phone_login(TEST_PHONE)
    assert result['status'] == 'VERIFICATION_REQUIRED'
    assert 'verify your phone number' in result['message'].lower()

@pytest.mark.asyncio
async def test_initiate_phone_login_not_found(cognito):
    """Test login initiation with non-existent user"""
    result = await initiate_phone_login("+919999999999")
    assert result['status'] == 'NOT_FOUND'
    assert 'not registered' in result['message'].lower()

@pytest.mark.asyncio
async def test_verify_phone_login_success(cognito):
    """Test successful phone login verification"""
    # Create and verify a user first
    await create_verified_user(cognito)
    
    # Initiate login
    await initiate_phone_login(TEST_PHONE)
    
    # Verify login
    result = await verify_phone_login(TEST_PHONE, TEST_VERIFICATION_CODE)
    assert result['status'] == 'SUCCESS'
    assert 'tokens' in result
    assert 'user' in result
    assert result['user']['phone_number'] == format_phone_number(TEST_PHONE)

@pytest.mark.asyncio
async def test_verify_phone_login_invalid_code(cognito):
    """Test phone login verification with invalid code"""
    # Create and verify a user first
    await create_verified_user(cognito)
    
    # Initiate login
    await initiate_phone_login(TEST_PHONE)
    
    # Test with invalid code
    with pytest.raises(Exception) as exc_info:
        await verify_phone_login(TEST_PHONE, "000000")
    assert "Invalid verification code" in str(exc_info.value)

@pytest.mark.asyncio
async def test_verify_phone_login_non_existent_user(cognito):
    """Test phone login verification for non-existent user"""
    with pytest.raises(Exception) as exc_info:
        await verify_phone_login("+919999999999", TEST_VERIFICATION_CODE)
    assert "User not found" in str(exc_info.value)

@pytest.mark.asyncio
@patch('requests.post')
async def test_handle_google_login_success(mock_post, cognito):
    """Test successful Google login"""
    # Create a verified Google user first
    google_tokens = {
        'access_token': 'test_access_token',
        'id_token': 'test_id_token',
        'refresh_token': 'test_refresh_token'
    }
    await complete_google_signup(TEST_EMAIL, TEST_PHONE, google_tokens)
    await verify_sms_code(TEST_PHONE, TEST_VERIFICATION_CODE, TEST_EMAIL)
    
    # Mock the token response for login
    mock_response = MagicMock()
    mock_response.status_code = 200
    
    # Create a mock ID token with test claims
    test_claims = {
        'email': TEST_EMAIL,
        'name': 'Test User',
        'picture': 'https://test.com/pic.jpg'
    }
    
    # Create a mock JWT token
    header = base64.b64encode(json.dumps({'alg': 'RS256'}).encode()).decode()
    payload = base64.b64encode(json.dumps(test_claims).encode()).decode()
    signature = base64.b64encode(b'signature').decode()
    id_token = f"{header}.{payload}.{signature}"
    
    mock_response.json.return_value = {
        'access_token': 'test_access_token',
        'id_token': id_token,
        'refresh_token': 'test_refresh_token'
    }
    mock_post.return_value = mock_response
    
    # Test Google login
    result = await handle_google_login(TEST_GOOGLE_CODE)
    assert result['status'] == 'SUCCESS'
    assert result['user']['email'] == TEST_EMAIL
    assert 'tokens' in result

@pytest.mark.asyncio
@patch('requests.post')
async def test_handle_google_login_unverified_phone(mock_post, cognito):
    """Test Google login with unverified phone"""
    # Create an unverified Google user
    google_tokens = {
        'access_token': 'test_access_token',
        'id_token': 'test_id_token',
        'refresh_token': 'test_refresh_token'
    }
    await complete_google_signup(TEST_EMAIL, TEST_PHONE, google_tokens)
    
    # Mock the token response for login
    mock_response = MagicMock()
    mock_response.status_code = 200
    
    # Create a mock ID token with test claims
    test_claims = {
        'email': TEST_EMAIL,
        'name': 'Test User',
        'picture': 'https://test.com/pic.jpg'
    }
    
    # Create a mock JWT token
    header = base64.b64encode(json.dumps({'alg': 'RS256'}).encode()).decode()
    payload = base64.b64encode(json.dumps(test_claims).encode()).decode()
    signature = base64.b64encode(b'signature').decode()
    id_token = f"{header}.{payload}.{signature}"
    
    mock_response.json.return_value = {
        'access_token': 'test_access_token',
        'id_token': id_token,
        'refresh_token': 'test_refresh_token'
    }
    mock_post.return_value = mock_response
    
    # Test Google login
    result = await handle_google_login(TEST_GOOGLE_CODE)
    assert result['status'] == 'PHONE_NUMBER_REQUIRED'
    assert result['email'] == TEST_EMAIL
    assert 'google_tokens' in result 