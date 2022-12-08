#!/bin/sh -e

if [ "$(id -u)" != 0 ]; then
    # echo "Already running as non-root, can't change user."
    exec "$@"
fi
# Create a non-root user with UID/GID matching the host user to ensure that
# files written to the volumes are not owned by root.
: "${cheribuild_uid:=1234}"
: "${cheribuild_gid:=1234}"
: "${cheribuild_user:=cheri}"
addgroup --quiet --gid ${cheribuild_gid} "${cheribuild_user}"
useradd --uid "${cheribuild_uid}" --gid "${cheribuild_gid}" --create-home --no-user-group --password '*' "${cheribuild_user}"

# Copy the cheribuild configuration to the unprivileged user's home directory:
export HOME="/home/${cheribuild_user}"
mkdir "${HOME}/.config"
cp -f /root/.config/cheribuild.json "${HOME}/.config/cheribuild.json"
chown -R "${cheribuild_uid}:${cheribuild_gid}" "${HOME}/.config"
# Run the actual command:
exec gosu "${cheribuild_user}" "$@"
