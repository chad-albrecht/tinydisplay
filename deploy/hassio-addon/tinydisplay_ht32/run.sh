#!/usr/bin/with-contenv bashio
# Run one bring-up command and exit. The output lands in the add-on log, which
# is the only console this machine has.
set -euo pipefail

COMMAND="$(bashio::config 'command')"

bashio::log.info "tinydisplay-ht32: running '${COMMAND}'"
bashio::log.info "hidraw nodes visible in this container:"
ls -l /dev/hidraw* 2>/dev/null || bashio::log.warning "  none -- the panel is not reachable from here"

case "${COMMAND}" in
  probe)
    # --open is the interesting half: enumeration succeeding while opening
    # fails is exactly what a permissions problem looks like.
    exec tinydisplay-ht32 probe --open
    ;;

  frame)
    exec tinydisplay-ht32 frame \
      --pattern "$(bashio::config 'pattern')" \
      --repeat "$(bashio::config 'repeat')"
    ;;

  led)
    exec tinydisplay-ht32 led \
      --theme "$(bashio::config 'theme')" \
      --intensity "$(bashio::config 'intensity')" \
      --speed "$(bashio::config 'speed')"
    ;;

  *)
    bashio::log.error "unknown command: ${COMMAND}"
    exit 2
    ;;
esac
