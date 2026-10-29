#pragma once

#include <array>
#include <atomic>
#include <mutex>

#include "../util/sync/sync_list.h"

#include "dxvk_bind_mask.h"
#include "dxvk_constant_state.h"
#include "dxvk_graphics_state.h"
#include "dxvk_pipecache.h"
#include "dxvk_pipelayout.h"
#include "dxvk_renderpass.h"
#include "dxvk_resource.h"
#include "dxvk_shader.h"
#include "dxvk_stats.h"

namespace dxvk {

  class DxvkDevice;
  class DxvkPipelineManager;

  /**
   * \brief Flags that describe pipeline properties
   */
  enum class DxvkGraphicsPipelineFlag {
    HasTransformFeedback,
    HasStorageDescriptors,
  };

  using DxvkGraphicsPipelineFlags = Flags<DxvkGraphicsPipelineFlag>;


  /**
   * \brief Shaders used in graphics pipelines
   */
  struct DxvkGraphicsPipelineShaders {
    Rc<DxvkShader> vs;
    Rc<DxvkShader> tcs;
    Rc<DxvkShader> tes;
    Rc<DxvkShader> gs;
    Rc<DxvkShader> fs;

    bool eq(const DxvkGraphicsPipelineShaders& other) const {
      return vs == other.vs && tcs == other.tcs
          && tes == other.tes && gs == other.gs
          && fs == other.fs;
    }

    size_t hash() const {
      DxvkHashState state;
      state.add(DxvkShader::getHash(vs));
      state.add(DxvkShader::getHash(tcs));
      state.add(DxvkShader::getHash(tes));
      state.add(DxvkShader::getHash(gs));
      state.add(DxvkShader::getHash(fs));
      return state;
    }

    bool validate() const {
      return validateShaderType(vs, VK_SHADER_STAGE_VERTEX_BIT)
          && validateShaderType(tcs, VK_SHADER_STAGE_TESSELLATION_CONTROL_BIT)
          && validateShaderType(tes, VK_SHADER_STAGE_TESSELLATION_EVALUATION_BIT)
          && validateShaderType(gs, VK_SHADER_STAGE_GEOMETRY_BIT)
          && validateShaderType(fs, VK_SHADER_STAGE_FRAGMENT_BIT);
    }

    static bool validateShaderType(const Rc<DxvkShader>& shader, VkShaderStageFlagBits stage) {
      return shader == nullptr || shader->info().stage == stage;
    }
  };


  /**
   * \brief Common graphics pipeline state
   *
   * Non-dynamic pipeline state that cannot
   * be changed dynamically.
   */
  struct DxvkGraphicsCommonPipelineStateInfo {
    bool                                msSampleShadingEnable;
    float                               msSampleShadingFactor;
  };


  /**
   * \brief Graphics pipeline instance
   *
   * Stores a state vector and the
   * corresponding pipeline handle.
   */
  class DxvkGraphicsPipelineInstance {

  public:

    DxvkGraphicsPipelineInstance()
    : m_stateVector (),
      m_renderPass  (nullptr),
      m_pipeline    (VK_NULL_HANDLE) { }

    DxvkGraphicsPipelineInstance(
      const DxvkGraphicsPipelineStateInfo&  state,
      const DxvkRenderPass*                 rp,
            VkPipeline                      pipe)
    : m_stateVector (state),
      m_renderPass  (rp),
      m_pipeline    (pipe) { }

    /**
     * \brief Checks for matching pipeline state
     *
     * \param [in] stateVector Graphics pipeline state
     * \param [in] renderPass Render pass handle
     * \returns \c true if the specialization is compatible
     */
    bool isCompatible(
      const DxvkGraphicsPipelineStateInfo&  state,
      const DxvkRenderPass*                 rp) {
      return m_renderPass  == rp
          && m_stateVector == state;
    }

    VkPipeline pipeline() const {
      return m_pipeline;
    }

    const DxvkRenderPass* renderPass() const {
      return m_renderPass;
    }

  private:

    DxvkGraphicsPipelineStateInfo m_stateVector;
    const DxvkRenderPass*         m_renderPass;
    VkPipeline                    m_pipeline;

  };


  /**
   * \brief Graphics pipeline
   *
   * Stores the pipeline layout as well as methods to
   * recompile the graphics pipeline against a given
   * pipeline state vector.
   */
  class DxvkGraphicsPipeline {

  public:

    DxvkGraphicsPipeline(
            DxvkPipelineManager*        pipeMgr,
            DxvkGraphicsPipelineShaders shaders);

    ~DxvkGraphicsPipeline();

    /**
     * \brief Shaders used by the pipeline
     * \returns Shaders used by the pipeline
     */
    const DxvkGraphicsPipelineShaders& shaders() const {
      return m_shaders;
    }

    /**
     * \brief Returns graphics pipeline flags
     * \returns Graphics pipeline property flags
     */
    DxvkGraphicsPipelineFlags flags() const {
      return m_flags;
    }

    /**
     * \brief Pipeline layout
     *
     * Stores the pipeline layout and the descriptor set
     * layout, as well as information on the resource
     * slots used by the pipeline.
     * \returns Pipeline layout
     */
    DxvkPipelineLayout* layout() const {
      return m_layout.ptr();
    }

    /**
     * \brief Queries shader for a given stage
     *
     * In case no shader is specified for the
     * given stage, \c nullptr will be returned.
     * \param [in] stage The shader stage
     * \returns Shader of the given stage
     */
    Rc<DxvkShader> getShader(
            VkShaderStageFlagBits             stage) const;

    VkPipeline getPipelineHandle(
      const DxvkGraphicsPipelineStateInfo&    state,
      const DxvkRenderPass*                   renderPass);

    /**
     * \brief Compiles a pipeline
     *
     * Asynchronously compiles the given pipeline
     * and stores the result for future use.
     * \param [in] state Pipeline state vector
     * \param [in] renderPass The render pass
     * \returns \c true if compile succeeded
     */
    bool compilePipeline(
      const DxvkGraphicsPipelineStateInfo&    state,
      const DxvkRenderPass*                   renderPass);

    void writePipelineStateToCache(
      const DxvkGraphicsPipelineStateInfo& state,
      const DxvkRenderPassFormat&          format) const;

  private:

    Rc<vk::DeviceFn>            m_vkd;
    DxvkPipelineManager*        m_pipeMgr;

    DxvkGraphicsPipelineShaders m_shaders;
    DxvkDescriptorSlotMapping   m_slotMapping;

    Rc<DxvkPipelineLayout>      m_layout;

    uint32_t m_vsIn  = 0;
    uint32_t m_fsOut = 0;

    DxvkGraphicsPipelineFlags           m_flags;
    DxvkGraphicsCommonPipelineStateInfo m_common;

    struct LockFreeMapEntry {
      std::atomic<size_t>                        hash     { 0 };
      std::atomic<DxvkGraphicsPipelineInstance*> instance { nullptr };
    };

    struct FallbackMapEntry {
      std::atomic<uintptr_t>  key      { 0 };
      std::atomic<VkPipeline> pipeline { VK_NULL_HANDLE };
      std::atomic<uint8_t>    used     { 0 };
    };

    /* One object of this class exists per shader combination the game uses and
     * lives as long as the device, so everything stored inline here is
     * multiplied by every combination a game touches. It used to carry 4096
     * instance slots, 8192 overflow slots, 2048 fallback slots and a 4096-entry
     * queued set: 272 KiB per combination. A game with a thousand of them held
     * a quarter of a gigabyte of metadata, and because that is far larger than
     * the console's shared 2 MiB L2, every lookup also walked into cold memory.
     * One combination realistically sees a handful of state vectors and one or
     * two render passes, so the tables are sized for that instead.
     *
     * The overflow table keeps the old capacity per probe and is only allocated
     * the first time the primary table fills up, so a game that does churn
     * hundreds of variants for one shader pair still gets its entries. */
    static constexpr uint32_t InstanceMapSize    = 256;
    static constexpr uint32_t InstanceMapMask    = InstanceMapSize - 1;
    static constexpr uint32_t MaxProbeDistance   = 24;
    static constexpr uint32_t OverflowMapSize    = 2048;
    static constexpr uint32_t OverflowMapMask    = OverflowMapSize - 1;
    static constexpr uint32_t OverflowProbeMax   = 64;
    static constexpr uint32_t FallbackMapSize    = 128;
    static constexpr uint32_t FallbackMapMask    = FallbackMapSize - 1;
    static constexpr uint32_t FallbackProbeMax   = 16;

    /* Only in flight for as long as a variant sits in the compiler queue, and
     * a collision merely defers one queue submission to a later draw. */
    static constexpr uint32_t QueuedSetSize = 64;
    static constexpr uint32_t QueuedSetMask = QueuedSetSize - 1;

    using OverflowMap = std::array<LockFreeMapEntry, OverflowMapSize>;

    sync::List<DxvkGraphicsPipelineInstance>  m_pipelines;

    std::array<std::atomic<size_t>, QueuedSetSize> m_queuedSet;

    std::array<LockFreeMapEntry,  InstanceMapSize>   m_instanceMap;
    std::atomic<OverflowMap*>                        m_overflowMap { nullptr };
    std::array<FallbackMapEntry,  FallbackMapSize>   m_fallbackMap;
    std::atomic<VkPipeline>                          m_basePipeline { VK_NULL_HANDLE };
    std::atomic<bool>                                m_hasBasePipeline { false };

    static size_t computeInstanceHash(
      const DxvkGraphicsPipelineStateInfo& state,
      const DxvkRenderPass*                renderPass);

    /* The hash walks the whole state vector, so callers that need to look up
     * and then insert compute it once and pass it on instead of hashing the
     * same 400-odd bytes three times. */
    DxvkGraphicsPipelineInstance* findInstanceLockFree(
            size_t                               hash,
      const DxvkGraphicsPipelineStateInfo& state,
      const DxvkRenderPass*                renderPass);

    void insertInstanceToMap(
            size_t                               hash,
      const DxvkGraphicsPipelineStateInfo& state,
      const DxvkRenderPass*                renderPass,
      DxvkGraphicsPipelineInstance*         inst);

    OverflowMap* overflowMap() const {
      return m_overflowMap.load(std::memory_order_acquire);
    }

    OverflowMap* acquireOverflowMap();

    DxvkGraphicsPipelineInstance* createInstance(
      const DxvkGraphicsPipelineStateInfo& state,
      const DxvkRenderPass*                renderPass);

    DxvkGraphicsPipelineInstance* findInstance(
      const DxvkGraphicsPipelineStateInfo& state,
      const DxvkRenderPass*                renderPass);

    static uintptr_t computeFallbackKey(
      const DxvkRenderPass*                renderPass);

    VkPipeline findFallback(
      const DxvkRenderPass*                renderPass);

    std::atomic<uint32_t>                          m_fallbackEvictCounter { 0 };

    VkPipeline createPipeline(
      const DxvkGraphicsPipelineStateInfo& state,
      const DxvkRenderPass*                renderPass) const;

    void destroyPipeline(
            VkPipeline                     pipeline) const;

    DxvkShaderModule createShaderModule(
      const Rc<DxvkShader>&                shader,
      const DxvkGraphicsPipelineStateInfo& state) const;

    Rc<DxvkShader> getPrevStageShader(
            VkShaderStageFlagBits          stage) const;

    bool validatePipelineState(
      const DxvkGraphicsPipelineStateInfo& state,
            bool                           trusted) const;

    void logPipelineState(
            LogLevel                       level,
      const DxvkGraphicsPipelineStateInfo& state) const;

  };

}
