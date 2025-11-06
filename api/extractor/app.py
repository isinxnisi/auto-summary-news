from fastapi import FastAPI
from routes.extract import router as extract_router
from routes.analyze import router as analyze_router
from routes.mt import router as mt_router

app = FastAPI(title="Text Extractor (Private)", version="0.2.0")
app.include_router(extract_router)
app.include_router(analyze_router)
app.include_router(mt_router)
