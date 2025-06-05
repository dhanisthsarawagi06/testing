import pytest
import boto3
import os
from moto import mock_aws
from fastapi.testclient import TestClient
from datetime import datetime
from app.main import app
from app.services.auth import get_current_user
from app.services.dynamodb import _get_dynamodb_client
from app.utils.s3_utils import S3Handler
from unittest.mock import MagicMock
import tempfile
from fastapi import Body

# Test data
TEST_DESIGN_ID = "test-design-1"
TEST_USER_EMAIL = "user@test.com"
TEST_DESIGN_PATH = "designs/test1.png"
TEST_THUMBNAIL_PATH = "thumbnails/test1.png"


@pytest.fixture(scope='function')
def aws_credentials():
    """Mocked AWS Credentials for moto."""
    os.environ['AWS_ACCESS_KEY_ID'] = 'testing'
    os.environ['AWS_SECRET_ACCESS_KEY'] = 'testing'
    os.environ['AWS_SECURITY_TOKEN'] = 'testing'
    os.environ['AWS_SESSION_TOKEN'] = 'testing'
    os.environ['AWS_DEFAULT_REGION'] = 'us-east-1'


@pytest.fixture(scope='function')
def mock_s3(aws_credentials):
    """Create mocked S3 client and buckets."""
    with mock_aws():
        # Create S3 client
        s3_client = boto3.client('s3', region_name='us-east-1')
        
        # Create test buckets
        s3_client.create_bucket(Bucket='designs')
        s3_client.create_bucket(Bucket='thumbnails')
        
        # Create a test file
        with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
            tmp_file.write(b"Test file content")
            tmp_file_path = tmp_file.name
        
        # Upload test files to S3
        s3_client.upload_file(tmp_file_path, 'designs', TEST_DESIGN_PATH)
        s3_client.upload_file(tmp_file_path, 'thumbnails', TEST_THUMBNAIL_PATH)
        
        # Clean up temporary file
        os.unlink(tmp_file_path)
        
        # Create mock S3 handler
        s3_mock = MagicMock()
        s3_mock.designs_bucket = "designs"
        s3_mock.thumbnails_bucket = "thumbnails"
        
        async def mock_generate_url(bucket, key, expiration=3600):
            # Generate an actual presigned URL using the mock S3 client
            url = s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': bucket,
                    'Key': key
                },
                ExpiresIn=expiration
            )
            return url
        
        s3_mock.generate_presigned_url = mock_generate_url
        
        # Override the S3Handler in the app
        app.dependency_overrides[S3Handler] = lambda: s3_mock
        
        return s3_mock


@pytest.fixture(scope='function')
def mock_db(aws_credentials):
    """Create mocked DynamoDB client and tables."""
    with mock_aws():
        dynamodb = boto3.client('dynamodb', region_name='us-east-1')
        
        # Create Design table
        dynamodb.create_table(
            TableName='Design',
            KeySchema=[
                {'AttributeName': 'design_id', 'KeyType': 'HASH'}
            ],
            AttributeDefinitions=[
                {'AttributeName': 'design_id', 'AttributeType': 'S'}
            ],
            ProvisionedThroughput={
                'ReadCapacityUnits': 5,
                'WriteCapacityUnits': 5
            }
        )

        # Create User table
        dynamodb.create_table(
            TableName='User',
            KeySchema=[
                {'AttributeName': 'email', 'KeyType': 'HASH'}
            ],
            AttributeDefinitions=[
                {'AttributeName': 'email', 'AttributeType': 'S'}
            ],
            ProvisionedThroughput={
                'ReadCapacityUnits': 5,
                'WriteCapacityUnits': 5
            }
        )

        # Add test design data
        dynamodb.put_item(
            TableName='Design',
            Item={
                'design_id': {'S': TEST_DESIGN_ID},
                'title': {'S': 'Test Design 1'},
                'verification_status': {'S': 'Pending'},
                'design_url': {'S': TEST_DESIGN_PATH},
                'thumbnail_url': {'S': TEST_THUMBNAIL_PATH},
                'seller_email': {'S': TEST_USER_EMAIL},
                'created_at': {'S': str(datetime.now())}
            }
        )

        # Add test user data
        dynamodb.put_item(
            TableName='User',
            Item={
                'email': {'S': 'designer@test.com'},
                'isDesigner': {'BOOL': True}
            }
        )

        def get_test_dynamodb():
            return dynamodb

        app.dependency_overrides[_get_dynamodb_client] = get_test_dynamodb
        
        yield dynamodb


# Mock authenticated users
async def mock_designer_user():
    return {
        "email": "designer@test.com",
        "sub": "test-designer-sub"
    }


async def mock_regular_user():
    return {
        "email": "user@test.com",
        "sub": "test-user-sub"
    }


# Create test client
client = TestClient(app)


def test_get_pending_designs(mock_db, mock_s3):
    """Test getting pending designs with S3 URLs"""
    app.dependency_overrides[get_current_user] = mock_designer_user
    
    response = client.get("/design/pending")
    assert response.status_code == 200
    
    data = response.json()
    assert "designs" in data
    assert len(data["designs"]) == 1
    design = data["designs"][0]
    
    # Verify design data
    assert design["id"] == TEST_DESIGN_ID
    assert "design_url" in design
    assert "thumbnail_url" in design
    assert design["design_url"].startswith("https://")
    assert TEST_DESIGN_PATH in design["design_url"]
    assert design["thumbnail_url"].startswith("https://")
    assert TEST_THUMBNAIL_PATH in design["thumbnail_url"]


def test_download_design(mock_db, mock_s3):
    """Test downloading a design file"""
    app.dependency_overrides[get_current_user] = mock_designer_user
    
    response = client.get(f"/design/download/{TEST_DESIGN_ID}")
    assert response.status_code == 200
    
    data = response.json()
    assert "download_url" in data
    assert data["download_url"].startswith("https://")
    assert TEST_DESIGN_PATH in data["download_url"]


def test_approve_design(mock_db):
    """Test approving a design"""
    app.dependency_overrides[get_current_user] = mock_designer_user
    
    response = client.post(
        f"/design/approve?design_id={TEST_DESIGN_ID}",
        headers={"Content-Type": "application/json"}
    )
    
    assert response.status_code == 200
    assert response.json()["message"] == "Design approved successfully"

    # Verify the design status was updated
    design = mock_db.get_item(
        TableName='Design',
        Key={'design_id': {'S': TEST_DESIGN_ID}}
    )['Item']
    assert design['verification_status']['S'] == 'Verified'
    assert design['verified_by']['S'] == 'designer@test.com'
    assert 'verified_at' in design


def test_reject_design(mock_db):
    """Test rejecting a design"""
    app.dependency_overrides[get_current_user] = mock_designer_user
    
    response = client.post(
        f"/design/reject?design_id={TEST_DESIGN_ID}&verification_comments=Design does not meet guidelines",
        headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 200
    assert response.json()["message"] == "Design rejected successfully"

    # Verify the design status was updated
    design = mock_db.get_item(
        TableName='Design',
        Key={'design_id': {'S': TEST_DESIGN_ID}}
    )['Item']
    assert design['verification_status']['S'] == 'Rejected'
    assert design['verified_by']['S'] == 'designer@test.com'
    assert design['verification_comments']['S'] == "Design does not meet guidelines"
    assert 'verified_at' in design


def test_non_designer_access(mock_db):
    """Test access restrictions for non-designers"""
    app.dependency_overrides[get_current_user] = mock_regular_user

    # Test pending designs access
    response = client.get("/design/pending")
    assert response.status_code == 403

    # Test approve access
    response = client.post(
        f"/design/approve?design_id={TEST_DESIGN_ID}",
        headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 403

    # Test reject access
    response = client.post(
        f"/design/reject?design_id={TEST_DESIGN_ID}&verification_comments=Test comment",
        headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 403


def test_invalid_design_id(mock_db):
    """Test handling invalid design IDs"""
    app.dependency_overrides[get_current_user] = mock_designer_user
    
    response = client.post(
        "/design/approve?design_id=non-existent-design",
        headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 404


def test_cleanup():
    """Clean up any test resources"""
    app.dependency_overrides = {}


if __name__ == "__main__":
    pytest.main(["-v"])
