from enum import Enum

class UserBadge(Enum):
    NOVICE = ("Novice", 10)
    KNIGHT = ("Knight", 100)
    SORCERER = ("Sorcerer", 1000)
    GUARDIAN = ("Guardian", 10000)
    OVERLORD = ("Overlord", 100000)

class CommunityService:
    def __init__(self, dynamodb_client):
        self.dynamodb = dynamodb_client

    def calculate_user_score(self, verified_designs: int, total_sold: int, total_credits: int) -> int:
        # Equal weightage to all three factors
        design_score = verified_designs * 0.1  # Base points for uploads
        sales_score = total_sold * 0.15        # Base points for sales
        credit_score = total_credits * 0.2     # Credits as is
        
        return int(design_score + sales_score + credit_score)

    def get_user_badge(self, verified_designs: int) -> dict:
        badge = None
        for badge_type in reversed(list(UserBadge)):
            if verified_designs >= badge_type.value[1]:
                badge = {
                    "name": badge_type.value[0],
                    "threshold": badge_type.value[1]
                }
                break
        return badge 