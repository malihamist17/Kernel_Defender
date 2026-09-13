savedcmd_defender_module.mod := printf '%s\n'   defender_module.o | awk '!x[$$0]++ { print("./"$$0) }' > defender_module.mod
