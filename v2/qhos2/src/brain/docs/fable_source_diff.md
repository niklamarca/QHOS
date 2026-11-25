# Fable source revision diff

Pinned sources never auto-apply to Brain behavior.

Process for a future leak/update:

1. Fetch the new file.
2. Record the new commit SHA.
3. Run `brain.fable.source.diff_revisions(old_pins, new_pins)`.
4. Inventory added / changed / removed sections.
5. Assign or update FAB IDs.
6. Update the integration matrix.
7. Add or adjust tests.
8. Run the offline Proving Ground.
9. Human review before any runtime change.

Auto-sync is forbidden.
