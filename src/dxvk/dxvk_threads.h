#pragma once

#include <algorithm>
#include <cstdint>

#include "../util/thread.h"
#include "../util/util_env.h"

namespace dxvk {

  /**
   * \brief Background compiler budget
   *
   * Threads that compile pipelines and shaders in the background fight the
   * draw thread for cores, and this fork targets machines where there are
   * only a few: three on the console it is tuned for. One core is treated as
   * the game's and is not handed out, unless the user asks for all of them.
   * \returns How many background threads the machine can afford
   */
  inline uint32_t compilerCoreBudget() {
    uint32_t cores = std::max(1u, dxvk::thread::hardware_concurrency());

    return env::getEnvVar("DXVK_ALL_CORES") == "1"
      ? cores
      : std::max(1u, cores - 1u);
  }


  /**
   * \brief Default background worker count
   *
   * Leaves one core to the game and takes most of the rest. The same for the
   * state cache warm-up and for whichever compilation method is active.
   */
  inline uint32_t autoCompilerThreads() {
    uint32_t cores = std::max(1u, dxvk::thread::hardware_concurrency());
    uint32_t count = ((cores - 1u) * 5u) / 7u;

    return std::clamp(count, 1u, 32u);
  }

}
