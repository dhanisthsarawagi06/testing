import json
import boto3
import numpy as np
from botocore.config import Config
from io import BytesIO
import base64
import os
import datetime
import time
from urllib.parse import unquote_plus
import requests

def get_embedding_from_modal(image_b64: str, modal_url: str) -> np.ndarray:
    try:
        print("Calling Modal fallback endpoint...")
        payload = {
            "mode": "image",
            "value": image_b64
        }
        headers = {"Content-Type": "application/json"}
        response = requests.post(modal_url, headers=headers, data=json.dumps(payload))
        response.raise_for_status()

        result = response.json()
        embedding = result.get("embedding")

        if not embedding:
            raise ValueError("No embedding returned from Modal API")

        print("Received embedding from Modal.")
        return np.array(embedding)
    except Exception as e:
        print(f"Modal fallback failed: {str(e)}")
        raise

def lambda_handler(event, context):
    try:
        print("=== Processing Textile Design Thumbnail for Embeddings ===")
        initial_delay = 90
        print(f"Waiting {initial_delay} seconds for DynamoDB record creation...")
        time.sleep(initial_delay)

        config = Config(
            connect_timeout=5,
            read_timeout=300,
            retries={'max_attempts': 3}
        )
        s3 = boto3.client('s3')
        runtime_sagemaker = boto3.client('sagemaker-runtime', config=config)
        dynamodb = boto3.client('dynamodb')

        records = event.get('Records', [])
        if not records:
            raise ValueError("No records found in event")

        for record in records:
            source_bucket = record['s3']['bucket']['name']
            source_key = unquote_plus(record['s3']['object']['key'])

            if source_key.startswith("search/"):
                return

            print(f"Processing: s3://{source_bucket}/{source_key}")

            is_dev = "dev" in source_bucket.lower()

            if source_bucket not in ['textile-marketplace-thumbnails', 'dev-textile-marketplace-thumbnails']:
                print(f"Skipping non-thumbnail bucket: {source_bucket}")
                continue

            # Set environment-based variables
            design_table = os.getenv('DEV_DESIGN_TABLE') if is_dev else os.getenv('DESIGN_TABLE')
            design_url = os.getenv('DEV_DESIGN_URL') if is_dev else os.getenv('DESIGN_URL')
            embedding_bucket = "embedding-stored-index-dev" if is_dev else "embedding-stored-index"
            metadata_bucket = "milvus-metadata-index-dev" if is_dev else "milvus-metadata-index"

            filename = os.path.basename(source_key)
            design_id = os.path.splitext(filename)[0]

            # Fetch design details from DynamoDB with retries
            max_retries = 3
            retry_delay = 60
            design_item = None

            for attempt in range(max_retries):
                try:
                    print(f"Attempt {attempt + 1} to fetch design from DynamoDB")
                    design_response = dynamodb.get_item(
                        TableName=design_table,
                        Key={'design_id': {'S': design_id}}
                    )
                    design_item = design_response.get('Item')
                    if design_item:
                        print(f"Design found in DynamoDB on attempt {attempt + 1}")
                        break
                    else:
                        if attempt < max_retries - 1:
                            wait_time = retry_delay * (attempt + 1)
                            print(f"Design not found, waiting {wait_time} seconds before retry...")
                            time.sleep(wait_time)
                except Exception as e:
                    print(f"Error fetching design from DynamoDB on attempt {attempt + 1}: {str(e)}")
                    if attempt < max_retries - 1:
                        wait_time = retry_delay * (attempt + 1)
                        print(f"Waiting {wait_time} seconds before retry...")
                        time.sleep(wait_time)
                    else:
                        raise

            if not design_item:
                print(f"Design not found in DynamoDB after {max_retries} attempts: {design_id}")
                continue

            # Read image content from S3
            response = s3.get_object(Bucket=source_bucket, Key=source_key)
            image_content = response['Body'].read()
            print(f"Image fetched, size: {len(image_content)} bytes")

            image_b64 = base64.b64encode(image_content).decode('utf-8')

            embedding = None
            try:
                print("Calling SageMaker endpoint...")
                response = runtime_sagemaker.invoke_endpoint(
                    EndpointName='clip-embedding-endpoint',
                    ContentType='application/json',
                    Body=json.dumps(image_b64)
                )
                print("Processing response...")
                result = json.loads(response['Body'].read().decode())
                embedding = np.array(result)
                print("✅ SageMaker returned embedding successfully.")
            except Exception as sagemaker_error:
                print(f"⚠️ SageMaker failed: {str(sagemaker_error)}")
                print("Falling back to Modal...")
                modal_url = os.getenv("MODAL_CLIP_URL")
                if not modal_url:
                    raise ValueError("MODAL_CLIP_URL environment variable not set")
                embedding = get_embedding_from_modal(image_b64, modal_url)

            print(f"✅ Final embedding shape: {embedding.shape}")
            print(f"Embedding norm: {np.linalg.norm(embedding)}")

            # Create embedding S3 key
            embedding_key = f"textile-marketplace/image-search/{filename.replace('.jpg', '.npy')}"
            print(f"Storing embedding at: s3://{embedding_bucket}/{embedding_key}")

            metadata_obj = {
                "org_id": "textile-marketplace",
                "collection_name": "image-search",
                "original_path": design_url + design_id,
                "thumbnail_bucket": source_bucket,
                "thumbnail_key": source_key,
                "upload_time": datetime.datetime.now().isoformat(),
                "file_hash": design_id,
                "metadata": {
                    "title": design_item['title']['S'],
                    "category": design_item['category']['S'],
                    "tags": design_item['tags']['S'].split(','),
                    "verification_status": design_item['verification_status']['S'],
                }
            }

            try:
                # Save embedding
                with BytesIO() as f:
                    np.save(f, embedding)
                    f.seek(0)
                    s3.put_object(
                        Bucket=embedding_bucket,
                        Key=embedding_key,
                        Body=f.read()
                    )
                print("✅ Embedding saved to S3.")

                # Save metadata
                metadata_key = f"textile-marketplace/image-search/{design_id}.json"
                s3.put_object(
                    Bucket=metadata_bucket,
                    Key=metadata_key,
                    Body=json.dumps(metadata_obj, indent=2),
                    ContentType='application/json'
                )
                print("✅ Metadata saved to S3.")

            except Exception as s3_error:
                print(f"Error saving to S3: {str(s3_error)}")
                raise

        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': 'Successfully processed all thumbnails',
                'processed_count': len(records)
            })
        }

    except Exception as e:
        print("=== Error in Lambda ===")
        print(f"Error: {str(e)}")
        import traceback
        print(f"Traceback: {traceback.format_exc()}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'error': str(e),
                'traceback': traceback.format_exc()
            })
        }
