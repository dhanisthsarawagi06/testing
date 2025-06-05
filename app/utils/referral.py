import random
import string

def generate_referral_code(length=8):
    """Generate a unique referral code"""
    characters = string.ascii_uppercase + string.digits
    return ''.join(random.choices(characters, k=length)) 