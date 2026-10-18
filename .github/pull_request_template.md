## What changed

<!-- One or two sentences. If the diff touches more than one concern, say so —
     this PR is easier to review when it does one thing. -->

## Why

<!-- The problem, not the solution. Link the issue if there is one. -->

## Gates

<!-- Required. Paste the actual output, not your belief that it passed.

     An agent that reports "lint passes" without output is indistinguishable
     from an agent that did not run it. The claim is the reviewable artefact. -->

- [ ] `just lint`
- [ ] `just typecheck`
- [ ] `just test`

```
paste output here
```

<!-- If a gate is knowingly red, stop and say why instead of ticking the box.
     "Pre-existing failure, not mine to fix" is a valid reason. Silence is not. -->

## Did you try to break it?

<!-- Required when this PR adds or changes a gate, a permission, or anything a
     test asserts. Describe what you changed and what you expected to fail.

     A gate that has never been seen to reject is not known to work. -->

- What did you deliberately break to check this gate actually fires?
- What was the output?
- Did you restore it?

```
paste output here
```

## Permissions

<!-- Required if you hit a `deny` and worked around it, or added a path.
     The permissions in opencode.json and .claude/settings.json are not
     arbitrary. If you want one changed, argue the case here rather than
     quietly editing around it — though note that editing those two files is
     itself denied. -->

- [ ] No permission was weakened or worked around
- [ ] Or: a permission was worked around, and why (explain below)

```
why, and where the rule should actually live
```

## Deliberate deviations

<!-- Anything you did that a reviewer will notice and think is wrong.

     Record it here with the reason. A deviation the reviewer discovers on their
     own costs more trust than one you explain up front. -->

```
none
```

## Not done, on purpose

<!-- If you stopped short of something the issue asked for, say which and why.
     Partial work that is silent about its edges reads as finished work. -->

```
none
```