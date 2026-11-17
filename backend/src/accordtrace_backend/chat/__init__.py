"""Bounded, source-traceable chat runtime. No startup provider calls."""
import os
os.environ['LANGSMITH_TRACING']='false'
os.environ['LANGCHAIN_TRACING_V2']='false'
