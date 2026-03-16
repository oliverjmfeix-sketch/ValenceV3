"""Read debug scalar batch files."""
import os
for i in range(10):
    path = f"/app/uploads/debug_scalar_batch_{i}.txt"
    if os.path.exists(path):
        with open(path) as f:
            content = f.read()
        print(f"\n{'='*60}")
        print(f"BATCH {i}: {len(content)} chars")
        print(f"{'='*60}")
        print(content[:5000])
        if len(content) > 5000:
            print(f"... ({len(content) - 5000} more chars)")
    else:
        break
