# memoryV4 GitHub deploy key

Repository: git@github.com:BartSchuster22/memoryV4.git
Private key path: /srv/memoryV4/.secrets/memoryV4_github_deploy_ed25519
Public key path: /srv/memoryV4/.secrets/memoryV4_github_deploy_ed25519.pub
Helper: /srv/memoryV4/bin/git-memoryV4

Add the public key below as a GitHub repository deploy key with **Allow write access** enabled.

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICjOBUJBVXzqQJZb6d2aK050i8GLO0AG3ftzAyAtVPE/ hermes-memoryV4-deploy-key-20260627
```

After GitHub deploy key is installed, agents can use:

```bash
cd /srv/memoryV4
/srv/memoryV4/bin/git-memoryV4 clone git@github.com:BartSchuster22/memoryV4.git repo
# or inside an existing checkout:
GIT_SSH_COMMAND='ssh -i /srv/memoryV4/.secrets/memoryV4_github_deploy_ed25519 -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new' git fetch origin
```
