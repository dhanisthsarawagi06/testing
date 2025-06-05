import boto3
from botocore.exceptions import ClientError
from fastapi import UploadFile
import json
from datetime import datetime
import os

s3_client = boto3.client(
    's3',
    aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
    aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
    region_name=os.getenv('AWS_REGION')
)

DESIGN_BUCKET = os.getenv('S3_DESIGN_BUCKET_NAME')
THUMBNAIL_BUCKET = os.getenv('S3_THUMBNAIL_BUCKET_NAME')

async def upload_design_files(
    design_file: UploadFile,
    thumbnail_file: UploadFile,
    metadata: str,
    title: str,
    user_email: str
) -> tuple[str, str]:

    # Generate filename with timestamp
    metadata_dict = json.loads(metadata)
    design_file_type = (metadata_dict['fileType'].lower() 
                     if 'fileType' in metadata_dict 
                     else metadata_dict['type'].split('/')[-1].lower())
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_title = title.replace(' ', '_').replace('/', '-').replace('\\', '-').lower()
    
    # Separate filenames for design and thumbnail
    design_filename = f"{safe_title}_{timestamp}.{design_file_type}"
    thumbnail_filename = f"{safe_title}_{timestamp}.jpg"
    
    # Create paths
    design_path = f"{user_email}/{design_filename}"
    thumbnail_path = f"{user_email}/{thumbnail_filename}"

    # Upload both files
    design_url = await upload_to_s3(
        file=design_file,
        bucket=DESIGN_BUCKET,
        path=design_path,
        content_type=design_file.content_type
    )

    thumbnail_url = await upload_to_s3(
        file=thumbnail_file,
        bucket=THUMBNAIL_BUCKET,
        path=thumbnail_path,
        content_type=thumbnail_file.content_type
    )

    return design_url, thumbnail_url

async def upload_to_s3(file: UploadFile, bucket: str, path: str, content_type: str) -> str:
    try:
        file_content = await file.read()
        
        s3_client.put_object(
            Bucket=bucket,
            Key=path,
            Body=file_content,
            ContentType=content_type
        )

        url = f"https://{bucket}.s3.{os.getenv('AWS_REGION')}.amazonaws.com/{path}"
        return url

    except ClientError as e:
        print(f"S3 upload error: {str(e)}")
        raise Exception(f"Failed to upload to S3: {str(e)}")
    finally:
        await file.seek(0)
