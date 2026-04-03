## Coding guidelines

### Think before coding
never guess never hide confusion
state assumptions explicitly ask if uncertain
if multiple interpretations exist present alternatives don't choose arbitrarily
suggest simpler approaches push back if needed
stop immediately and ask specific questions when unclear

### Simplicity first
write minimal code to solve the problem
no speculative code no unrequested features
no abstractions for single-use code
no unrequested flexibility or configurability
no error handling for impossible scenarios
if 50 lines suffice never write 200 rewrite from senior engineer perspective

### Surgical changes
modify only what is necessary clean only your own traces
never improve adjacent code comments formatting arbitrarily
follow existing project style strictly even if you disagree
@Author header must be devyoon91 (do not modify existing author names)
mention dead code but never delete it yourself
always remove imports variables functions made unused by your changes

### Code quality
prefer linux commands for simple tasks instead of python
use proper error handling only for realistic failure modes
write self-documenting code minimize comments
