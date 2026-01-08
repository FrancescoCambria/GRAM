
import os
from dotenv import load_dotenv
from neo4j import GraphDatabase

def test_connection():
    """Tests the connection to the Neo4j database."""
    load_dotenv()
    uri = os.getenv("NEO4J_URI")
    user = os.getenv("NEO4J_USER")
    password = os.getenv("NEO4J_PASSWORD")

    if not all([uri, user, password]):
        print("Error: NEO4J_URI, NEO4J_USER, or NEO4J_PASSWORD not found in .env file.")
        return

    print(f"Attempting to connect to Neo4j at {uri}...")
    try:
        driver = GraphDatabase.driver(uri, auth=(user, password))
        driver.verify_connectivity()
        print("✅ Connection successful!")
    except Exception as e:
        print(f"❌ Connection failed: {e}")
    finally:
        if 'driver' in locals() and driver:
            driver.close()

if __name__ == "__main__":
    test_connection()
