"""One local model request at a time, shared by extraction and chat."""
import threading

MODEL_LOCK=threading.Lock()
