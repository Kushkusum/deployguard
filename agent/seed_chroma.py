import json
import os
import chromadb
from chromadb.utils import embedding_functions

CHROMA_PATH = os.getenv("CHROMA_DB_PATH", os.path.expanduser("~/deployguard_data/chroma_db"))
client = chromadb.PersistentClient(path=CHROMA_PATH)
embed_fn = embedding_functions.OllamaEmbeddingFunction(
    url="http://localhost:11434/api/embeddings",
    model_name="nomic-embed-text",
)
collection = client.get_or_create_collection(name="incidents", embedding_function=embed_fn)

with open("incidents/incidents.json") as f:
    incidents = json.load(f)

collection.add(
    ids=[i["id"] for i in incidents],
    documents=[i["summary"] for i in incidents],
    metadatas=[{"root_cause": i["root_cause"], "resolution": i["resolution"]} for i in incidents],
)
print(f"Seeded {len(incidents)} incidents into Chroma.")
