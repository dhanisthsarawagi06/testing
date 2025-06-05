from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routes import auth, design, user, cart, transaction, admin, sales, community, collections
import uvicorn

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # You can restrict to specific domains in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/auth", tags=["authentication"])
app.include_router(design.router, prefix="/design", tags=["design"])
app.include_router(user.router, prefix="/user", tags=["user"])
app.include_router(cart.router, prefix="/cart", tags=["cart"])
app.include_router(transaction.router, prefix="/transaction", tags=["transaction"])
app.include_router(admin.router, prefix="/admin", tags=["admin"])
app.include_router(sales.router, prefix="/sales", tags=["sales"])
app.include_router(community.router, prefix="/community", tags=["community"])
app.include_router(collections.router, prefix="/collections", tags=["collections"])

@app.get("/")
def Home():
    return {"message": "Hello, World!"}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, reload=True)


