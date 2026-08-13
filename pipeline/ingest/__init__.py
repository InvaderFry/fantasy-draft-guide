"""Source adapters (S79 Step 2).

Nothing downstream binds to one provider: adapters fetch raw bytes, persist
them into a dated snapshot, and hand back parsed rows. Raw payloads are always
stored before parsing so a source question can be answered from the archive
rather than from a re-fetch that may no longer be possible.
"""
