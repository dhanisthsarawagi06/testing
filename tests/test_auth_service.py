import pytest
import boto3
import os
from moto import congitoidp
from app.services.auth import (
    user_sign_up,
    verify_sms_code,
    resend_verification_code,
    get_google_auth_url,
    handle_google_callback,
    complete_google_signup,
    format_phone_number,
    get_secret_hash
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
    with cognitoidp():
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

@pytest.mark.asyncio
async def test_user_sign_up(cognito):
    """Test user signup process"""
    # Test data
    signup_data = MagicMock(
        mobile=TEST_PHONE,
        email=TEST_EMAIL,
        password=TEST_PASSWORD
    )
    
    # Test signup
    result = await user_sign_up(signup_data)
    assert result['status'] == 'VERIFICATION_REQUIRED'
    assert result['phone_number'] == format_phone_number(TEST_PHONE)
    assert 'user_sub' in result

@pytest.mark.asyncio
async def test_verify_sms_code(cognito):
    """Test SMS verification"""
    # First create a user
    signup_data = MagicMock(
        mobile=TEST_PHONE,
        email=TEST_EMAIL,
        password=TEST_PASSWORD
    )
    await user_sign_up(signup_data)
    
    # Test verification
    result = await verify_sms_code(TEST_PHONE, TEST_VERIFICATION_CODE)
    assert result['status'] == 'SUCCESS'
    assert 'Phone number verified successfully' in result['message']

@pytest.mark.asyncio
async def test_resend_verification_code(cognito):
    """Test resending verification code"""
    # First create a user
    signup_data = MagicMock(
        mobile=TEST_PHONE,
        email=TEST_EMAIL,
        password=TEST_PASSWORD
    )
    await user_sign_up(signup_data)
    
    # Test resend
    result = await resend_verification_code(TEST_PHONE)
    assert result['status'] == 'SUCCESS'
    assert 'Verification code sent successfully' in result['message']

@pytest.mark.asyncio
async def test_google_auth_url():
    """Test Google auth URL generation"""
    result = await get_google_auth_url()
    assert 'url' in result
    assert 'oauth2/authorize' in result['url']
    assert 'identity_provider=Google' in result['url']

@pytest.mark.asyncio
@patch('requests.post')
async def test_handle_google_callback(mock_post, cognito):
    """Test Google OAuth callback handling"""
    # Mock the token response
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
    
    # Test callback
    result = await handle_google_callback(TEST_GOOGLE_CODE)
    assert result['status'] == 'PHONE_NUMBER_REQUIRED'
    assert result['email'] == TEST_EMAIL

@pytest.mark.asyncio
async def test_complete_google_signup(cognito):
    """Test completing Google signup with phone verification"""
    # Mock Google tokens
    google_tokens = {
        'access_token': 'test_access_token',
        'id_token': 'test_id_token',
        'refresh_token': 'test_refresh_token'
    }
    
    # Test completion
    result = await complete_google_signup(TEST_EMAIL, TEST_PHONE, google_tokens)
    assert result['status'] == 'VERIFICATION_REQUIRED'
    assert result['phone_number'] == format_phone_number(TEST_PHONE)

@pytest.mark.asyncio
async def test_format_phone_number():
    """Test phone number formatting"""
    # Test Indian number without country code
    assert format_phone_number("9876543210") == "+919876543210"
    
    # Test number with country code
    assert format_phone_number("+919876543210") == "+919876543210"
    
    # Test international number
    assert format_phone_number("+12025550123") == "+12025550123"

def test_get_secret_hash():
    """Test secret hash generation"""
    username = "testuser"
    result = get_secret_hash(username)
    assert isinstance(result, str)
    assert len(result) > 0

@pytest.mark.asyncio
async def test_user_sign_up_existing_user(cognito):
    """Test signup with existing user"""
    # Create first user
    signup_data = MagicMock(
        mobile=TEST_PHONE,
        email=TEST_EMAIL,
        password=TEST_PASSWORD
    )
    await user_sign_up(signup_data)
    
    # Try to create same user again
    with pytest.raises(Exception) as exc_info:
        await user_sign_up(signup_data)
    assert "already exists" in str(exc_info.value)

@pytest.mark.asyncio
async def test_verify_sms_code_invalid_code(cognito):
    """Test verification with invalid code"""
    # First create a user
    signup_data = MagicMock(
        mobile=TEST_PHONE,
        email=TEST_EMAIL,
        password=TEST_PASSWORD
    )
    await user_sign_up(signup_data)
    
    # Test with invalid code
    with pytest.raises(Exception) as exc_info:
        await verify_sms_code(TEST_PHONE, "000000")
    assert "Invalid verification code" in str(exc_info.value)

@pytest.mark.asyncio
async def test_verify_sms_code_non_existent_user(cognito):
    """Test verification for non-existent user"""
    with pytest.raises(Exception) as exc_info:
        await verify_sms_code("+919999999999", TEST_VERIFICATION_CODE)
    assert "User not found" in str(exc_info.value)

@pytest.mark.asyncio
async def test_resend_verification_code_non_existent_user(cognito):
    """Test resend code for non-existent user"""
    with pytest.raises(Exception) as exc_info:
        await resend_verification_code("+919999999999")
    assert "User not found" in str(exc_info.value)

@pytest.mark.asyncio
@patch('requests.post')
async def test_handle_google_callback_invalid_code(mock_post):
    """Test Google callback with invalid code"""
    # Mock failed token response
    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.text = "Invalid code"
    mock_post.return_value = mock_response
    
    with pytest.raises(Exception) as exc_info:
        await handle_google_callback("invalid_code")
    assert "Failed to exchange code for tokens" in str(exc_info.value)

@pytest.mark.asyncio
async def test_complete_google_signup_invalid_email(cognito):
    """Test Google signup completion with invalid email"""
    google_tokens = {
        'access_token': 'test_access_token',
        'id_token': 'test_id_token',
        'refresh_token': 'test_refresh_token'
    }
    
    with pytest.raises(Exception) as exc_info:
        await complete_google_signup("invalid@email", TEST_PHONE, google_tokens)
    assert "Failed to complete Google signup" in str(exc_info.value) 