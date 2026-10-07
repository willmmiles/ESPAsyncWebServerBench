# Writes firmware.factory.bin, the merged image collect.py flashes at 0x0, for
# platforms that do not (espressif32 6.x).  pioarduino writes its own.
Import("env")

board = env.BoardConfig()


def merge(target, source, env):
    images = [(o, env.subst(p)) for o, p in env.get("FLASH_EXTRA_IMAGES", [])]
    images.append(("$ESP32_APP_OFFSET", str(target[0])))
    env.Execute(env.VerboseAction(" ".join([
        '"$PYTHONEXE" "$OBJCOPY"', "--chip", board.get("build.mcu"), "merge_bin",
        "--flash_mode", "${__get_board_flash_mode(__env__)}",
        "--flash_freq", "${__get_board_f_image(__env__)}",
        "--flash_size", board.get("upload.flash_size", "4MB"),
        "-o", "$BUILD_DIR/${PROGNAME}.factory.bin",
    ] + [f'{o} "{p}"' for o, p in images]), "Merging $BUILD_DIR/${PROGNAME}.factory.bin"))


env.AddPostAction("$BUILD_DIR/${PROGNAME}.bin", merge)
