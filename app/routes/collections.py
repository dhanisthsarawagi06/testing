from fastapi import APIRouter, Depends, HTTPException
from typing import List
from datetime import datetime
import pytz
from app.schemas.collection import CollectionCreate, CollectionDesign, Collection
from app.services.auth import get_current_user
from app.services import dynamodb as dynamodb_service
import os

COLLECTION_TABLE = os.getenv('DYNAMODB_COLLECTION_TABLE')
DESIGN_TABLE = os.getenv('DYNAMODB_DESIGN_TABLE')


router = APIRouter()

@router.get("")
async def get_collections(current_user: dict = Depends(get_current_user)):
    try:
        user_email = current_user['email']

        # Query collections for the user
        response = dynamodb_service.query(
            table_name=COLLECTION_TABLE,
            key_condition_expression='#user_email = :email',
            expression_attribute_names={
                '#user_email': 'user_email'
            },
            expression_attribute_values={
                ':email': {'S': user_email}
            }
        )

        collections = []
        for item in response:
            collection_data = {
                'name': item['collection_name']['S'],
                'description': item.get('description', {}).get('S', ''),
                'design_count': int(item.get('design_count', {}).get('N', '0')),
                'created_at': item['created_at']['S'],
                'updated_at': item['updated_at']['S'],
            }
            collections.append(collection_data)

        return {"collections": collections}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("")
async def create_collection(
    collection: CollectionCreate,
    current_user: dict = Depends(get_current_user)
):
    try:
        user_email = current_user['email']
        now = datetime.now(pytz.timezone('Asia/Kolkata')).isoformat()

        # Check if collection already exists
        existing = dynamodb_service.get_item(
            table_name=COLLECTION_TABLE,
            key={
                'user_email': {'S': user_email},
                'collection_name': {'S': collection.name}
            }
        )


        if existing:
            raise HTTPException(status_code=400, detail="Collection with this name already exists")
    
        # Create collection item
        collection_item = {
            'user_email': {'S': user_email},
            'collection_name': {'S': collection.name},
            'design_count': {'N': '0'},
            'created_at': {'S': now},
            'updated_at': {'S': now},
            'designs': {'L': []}  # Empty list for designs
        }

        if collection.description:
            collection_item['description'] = {'S': collection.description}

        # Save to DynamoDB
        dynamodb_service.put_item(
            table_name=COLLECTION_TABLE,
            item=collection_item
        )



        return {
            "name": collection.name,
            "description": collection.description,
            "design_count": 0,
            "created_at": now,
            "updated_at": now
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{collection_name}/designs")
async def add_design_to_collection(
    collection_name: str,
    design: CollectionDesign,
    current_user: dict = Depends(get_current_user)
):
    try:
        user_email = current_user['email']
        now = datetime.now(pytz.timezone('Asia/Kolkata')).isoformat()

        # Verify collection exists and belongs to user
        collection = dynamodb_service.get_item(
            table_name=COLLECTION_TABLE,
            key={
                'user_email': {'S': user_email},
                'collection_name': {'S': collection_name}
            }
        )

        if not collection:
            raise HTTPException(status_code=404, detail="Collection not found")

        # Verify design exists
        design_item = dynamodb_service.get_item(
            table_name=DESIGN_TABLE,
            key={
                'design_id': {'S': design.design_id}
            }
        )

        if not design_item:
            raise HTTPException(status_code=404, detail="Design not found")

        # Check if design already exists in collection
        existing_designs = collection.get('designs', {}).get('L', [])
        if any(d.get('M', {}).get('design_id', {}).get('S') == design.design_id for d in existing_designs):
            raise HTTPException(status_code=400, detail="Design already in collection")

        # Add design to collection
        new_design = {
            'M': {
                'design_id': {'S': design.design_id},
                'added_at': {'S': now}
            }
        }

        # Update collection with new design
        dynamodb_service.update_item(
            table_name=COLLECTION_TABLE,
            key={
                'user_email': {'S': user_email},
                'collection_name': {'S': collection_name}
            },
            update_expression='SET #designs = list_append(if_not_exists(designs, :empty_list), :new_design), #design_count = if_not_exists(design_count, :zero) + :inc, #updated_at = :now',
            expression_attribute_names={
                '#designs': 'designs',
                '#design_count': 'design_count',
                '#updated_at': 'updated_at'
            },
            expression_attribute_values={
                ':new_design': {'L': [new_design]},
                ':empty_list': {'L': []},
                ':inc': {'N': '1'},
                ':zero': {'N': '0'},
                ':now': {'S': now}
            }
        )

        return {"message": "Design added to collection successfully"}

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{collection_name}/designs")
async def get_collection_designs(
    collection_name: str,
    current_user: dict = Depends(get_current_user)
):
    try:
        user_email = current_user['email']

        # Get collection and its designs
        collection = dynamodb_service.get_item(
            table_name=COLLECTION_TABLE,
            key={
                'user_email': {'S': user_email},
                'collection_name': {'S': collection_name}
            }
        )


        if not collection:
            raise HTTPException(status_code=404, detail="Collection not found")

        designs = []
        for design_item in collection.get('designs', {}).get('L', []):
            design_id = design_item['M']['design_id']['S']
            added_at = design_item['M']['added_at']['S']
            
            design_data = dynamodb_service.get_item(
                table_name=DESIGN_TABLE,
                key={
                    'design_id': {'S': design_id}
                }
            )

            
            if design_data:
                designs.append({
                    'id': design_id,
                    'title': design_data['title']['S'],
                    'thumbnail_url': design_data['thumbnail_url']['S'],
                    'price': float(design_data['price']['N']),
                    'added_at': added_at
                })

        return {"designs": designs}

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) 

@router.delete("/{collection_name}")
async def delete_collection(
    collection_name: str,
    current_user: dict = Depends(get_current_user)
):
    try:
        user_email = current_user['email']

        # Verify collection exists and belongs to user
        collection = dynamodb_service.get_item(
            table_name=COLLECTION_TABLE,
            key={
                'user_email': {'S': user_email},
                'collection_name': {'S': collection_name}
            }
        )

        if not collection:
            raise HTTPException(status_code=404, detail="Collection not found")

        # Delete the collection
        dynamodb_service.delete_item(
            table_name=COLLECTION_TABLE,
            key={
                'user_email': {'S': user_email},
                'collection_name': {'S': collection_name}
            }
        )

        return {"message": "Collection deleted successfully"}

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{collection_name}/designs/{design_id}")
async def remove_design_from_collection(
    collection_name: str,
    design_id: str,
    current_user: dict = Depends(get_current_user)
):
    try:
        user_email = current_user['email']

        # Get collection and verify ownership
        collection = dynamodb_service.get_item(
            table_name=COLLECTION_TABLE,
            key={
                'user_email': {'S': user_email},
                'collection_name': {'S': collection_name}
            }
        )

        if not collection:
            raise HTTPException(status_code=404, detail="Collection not found")

        # Filter out the design to be removed
        existing_designs = collection.get('designs', {}).get('L', [])
        updated_designs = [
            d for d in existing_designs 
            if d.get('M', {}).get('design_id', {}).get('S') != design_id
        ]

        # Update collection with filtered designs and decremented count
        dynamodb_service.update_item(
            table_name=COLLECTION_TABLE,
            key={
                'user_email': {'S': user_email},
                'collection_name': {'S': collection_name}
            },
            update_expression='SET #designs = :designs, #design_count = #design_count - :dec, #updated_at = :now',
            expression_attribute_names={
                '#designs': 'designs',
                '#design_count': 'design_count',
                '#updated_at': 'updated_at'
            },
            expression_attribute_values={
                ':designs': {'L': updated_designs},
                ':dec': {'N': '1'},
                ':now': {'S': datetime.now(pytz.timezone('Asia/Kolkata')).isoformat()}
            }
        )

        return {"message": "Design removed from collection successfully"}

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) 