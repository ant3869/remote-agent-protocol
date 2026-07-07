# Wargame Simulator

Read the task brief from `/tasks/` and generate a move-by-move wargame document:
1. Do not write a blue-sky, linear plan. Fight the mission on paper move-by-move.
2. For every step, use the Action-Reaction-Counteraction loop:
   - **Move / Action**: The step to be taken.
   - **Expected Observation**: Exactly what you should see if it worked, and if it failed.
   - **Likely Failure & Counter Move**: The most likely failure, its cause, and the counteraction to resolve the error.
   - **Forks & Triggers**: If X, then Y.
3. Flag any missing variables or assumptions to `ledger.md`.
4. End with strict **Abort Conditions**.
5. Save the document to `/war_games/`.