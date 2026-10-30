#include <algorithm>
#include <utility>

#include "dxvk_device.h"
#include "dxvk_graphics.h"
#include "dxvk_asynccompiler.h"
#include "dxvk_threads.h"

namespace dxvk {

  DxvkAsyncCompiler::DxvkAsyncCompiler(const DxvkDevice* device) {
    uint32_t numWorkers = autoCompilerThreads();

    // Reduce worker count on 32-bit to save address space
    if (env::is32BitHostPlatform())
      numWorkers = std::min(numWorkers, 16u);

    if (device->config().numShaderCompilerThreads > 0)
      numWorkers = uint32_t(device->config().numShaderCompilerThreads);

    if (env::getEnvVar("DXVK_ALL_CORES") == "1")
      numWorkers = std::max(1u, dxvk::thread::hardware_concurrency());

    Logger::info(str::format("DXVK: Using ", numWorkers, " async compiler threads"));

    m_compilerThreads.reserve(numWorkers);

    for (uint32_t i = 0; i < numWorkers; i++)
      m_compilerThreads.emplace_back([this] { this->runCompilerThread(); });
  }


  DxvkAsyncCompiler::~DxvkAsyncCompiler() {
    {
      std::lock_guard<std::mutex> lock(m_compilerLock);
      m_compilerStop.store(true);
    }

    m_compilerCond.notify_all();

    for (auto& thread : m_compilerThreads)
      thread.join();
  }


  void DxvkAsyncCompiler::queueCompilation(
    DxvkGraphicsPipeline*                pipeline,
    const DxvkGraphicsPipelineStateInfo& state,
    const DxvkRenderPass*                renderPass) {
    {
      std::lock_guard<std::mutex> lock(m_compilerLock);
      m_compilerQueue.emplace(PipelineEntry{ pipeline, state, renderPass });
    }

    m_compilerCond.notify_one();
  }


  void DxvkAsyncCompiler::runCompilerThread() {
    env::setThreadName("dxvk-pcompiler");

    while (true) {
      std::vector<PipelineEntry> tasks;

      {
        std::unique_lock<std::mutex> lock(m_compilerLock);

        m_compilerCond.wait(lock, [this] {
          return m_compilerStop.load() || !m_compilerQueue.empty();
        });

        if (m_compilerStop.load() && m_compilerQueue.empty())
          break;

        while (!m_compilerQueue.empty()) {
          tasks.push_back(std::move(m_compilerQueue.front()));
          m_compilerQueue.pop();
        }
      }

      for (auto& entry : tasks) {
        if (entry.pipeline && entry.renderPass) {
          if (entry.pipeline->compilePipeline(entry.state, entry.renderPass))
            entry.pipeline->writePipelineStateToCache(entry.state, entry.renderPass->format());
        }
      }
    }
  }

}
