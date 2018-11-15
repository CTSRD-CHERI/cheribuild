# $FreeBSD$
#
# System-wide .cshrc file for csh(1).

# Handle common escape sequences sent by many terminal emulators:
bindkey "\e[1~" beginning-of-line  # Home
bindkey "\e[7~" beginning-of-line  # Home rxvt
bindkey "\e[2~" overwrite-mode     # Ins
bindkey "\e[3~" delete-char        # Delete
bindkey "\e[4~" end-of-line        # End
bindkey "\e[8~" end-of-line        # End rxvt

# Map ctrl-left-arrow and ctrl-right-arrow to move words
bindkey "\e[1;5C" forward-word
bindkey "\e[1;5D" backward-word
bindkey "\e[5C" forward-word
bindkey "\e[5D" backward-word
bindkey "\e\e[C" forward-word
bindkey "\e\e[D" backward-word



alias cd..	cd ..
alias mount-sources	"qemu-mount-sources.sh"
alias mount-rootfs	"qemu-mount-rootfs.sh"
alias do-reroot "qemu-do-reroot.sh"
