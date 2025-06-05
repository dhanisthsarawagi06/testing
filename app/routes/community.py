from fastapi import APIRouter, Depends, HTTPException
from app.services.community import CommunityService
from app.services import dynamodb as dynamodb_service
from app.services.auth import get_current_user
from app.services.payment import PaymentService
import os


router = APIRouter()
community_service = CommunityService(dynamodb_service._get_dynamodb_client())
payment_service = PaymentService(dynamodb_service._get_dynamodb_client())
USERS_TABLE = os.getenv('DYNAMODB_USER_TABLE')
DESIGN_TABLE = os.getenv('DYNAMODB_DESIGN_TABLE')

@router.get("/leaderboard")
async def get_leaderboard(current_user: dict = Depends(get_current_user)):
    try:
        users = dynamodb_service.scan_table(USERS_TABLE)
        leaderboard = []
        current_user_entry = None

        for user in users:
            email = user.get('email', {}).get('S')
            username = user.get('username', {}).get('S')

            # Get all verified designs for this user
            designs = dynamodb_service.query_table(
                table_name=DESIGN_TABLE,
                index_name='DesignSellerGSI',
                key_condition_expression='seller_email = :email',
                expression_attribute_values={
                    ':email': {'S': email}
                }
            )

            verified_designs = 0
            total_sold = 0
            total_credits = 0

            for design in designs:
                if design.get('verification_status', {}).get('S') == 'Verified':
                    verified_designs += 1
                    total_sold += int(design.get('total_sold', {}).get('N', '0'))
                    price = float(design.get('price', {}).get('N', 0))
                    payment_method = design.get('payment_method', {}).get('S', '')
                    _, credits = payment_service.calculate_payment_and_credits(
                        price,
                        payment_method
                    )
                    total_credits += credits

            if verified_designs > 0:
                score = community_service.calculate_user_score(
                    verified_designs, 
                    total_sold,
                    total_credits
                )
                entry = {
                    'username': username,
                    'verified_designs': verified_designs,
                    'total_sold': total_sold,
                    'total_credits': total_credits,
                    'score': score,
                    'badge': community_service.get_user_badge(verified_designs),
                    'is_current_user': email == current_user['email']
                }
                leaderboard.append(entry)

        # Sort leaderboard by score
        leaderboard.sort(key=lambda x: x['score'], reverse=True)
        
        # Add ranks
        for i, entry in enumerate(leaderboard, 1):
            entry['rank'] = i
            if entry['is_current_user']:
                current_user_entry = entry

        # Get top 50 and append current user if not in top 50
        top_50 = leaderboard[:50]
        if current_user_entry and current_user_entry['rank'] > 50:
            top_50.append(current_user_entry)

        return {
            "leaderboard": top_50,
            "total_users": len(leaderboard)
        }

    except Exception as e:
        print(f"Leaderboard error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e)) 