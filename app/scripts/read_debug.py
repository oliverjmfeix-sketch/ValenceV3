"""Read debug scalar batch files and search for specific questions."""
import os
import sys

search = sys.argv[1] if len(sys.argv) > 1 else None

for i in range(10):
    path = f"/app/uploads/debug_scalar_batch_{i}.txt"
    if os.path.exists(path):
        with open(path) as f:
            content = f.read()
        if search:
            if search in content:
                print(f"\nBATCH {i}: FOUND '{search}'")
                # Find context around the match
                idx = content.find(search)
                start = max(0, idx - 200)
                end = min(len(content), idx + 500)
                print(content[start:end])
            else:
                print(f"BATCH {i}: not found")
        else:
            print(f"\n{'='*60}")
            print(f"BATCH {i}: {len(content)} chars")
            print(f"{'='*60}")
            print(content[:5000])
            if len(content) > 5000:
                print(f"... ({len(content) - 5000} more chars)")
    else:
        break
