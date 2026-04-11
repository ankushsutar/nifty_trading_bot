from pymongo import MongoClient
import os
from bot.config.settings import Config

print("Clearing test trades from MongoDB...")
client = MongoClient(Config.MONGO_URI)
db = client[Config.get_mongo_db()]
collection = db[Config.MONGO_COLLECTION]

# delete trades created today for testing
collection.delete_many({"symbol": "NIFTY", "mode": "PAPER"})
print("Done.")

