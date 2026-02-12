import json
import os
import tempfile
import shutil

def write_json_atomic(filepath, data):
    """
    Writes data to a temporary file first, then renames it to the target file.
    This ensures that the target file is never in a partial/corrupted state.
    """
    dir_name = os.path.dirname(os.path.abspath(filepath)) or '.'
    
    # Create temp file in the same directory to ensure atomic move
    with tempfile.NamedTemporaryFile('w', dir=dir_name, delete=False, suffix='.tmp') as tf:
        json.dump(data, tf, indent=4)
        temp_name = tf.name
        
    try:
        # Atomic replacement
        shutil.move(temp_name, filepath)
    except Exception as e:
        os.remove(temp_name)
        raise e
