# Wargame Executor

Execute a fully simulated wargame plan from the `/war_games/` folder:
1. **Review the Ledger:** Read `ledger.md`. Ensure all inputs are resolved. Ask the user if blockers remain.
2. **Follow the Moves:** Execute the plan strictly move-by-move.
3. **Observe and React:** Compare reality against the "Expected Observation".
4. **Counteractions:** If reality throws an error, immediately consult the "Likely Failure & Counter Move" section and execute the defined counteraction.
5. **Abort Conditions:** If abort conditions are met, stop execution entirely and report the trigger.