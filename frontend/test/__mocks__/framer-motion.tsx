import React from 'react'

// framer-motion 的 motion.* 在测试环境里不需要真实动画：mock 直接渲染目标
// DOM 元素（React.createElement(prop)）。framer-motion 专有 props
// （initial/animate/...）会被剥离，避免 React 对未知 DOM prop 告警。
const MOTION_SPECIFIC_PROPS = new Set([
  'initial', 'animate', 'exit', 'transition', 'variants',
  'whileHover', 'whileTap', 'layout',
])

export const motion = new Proxy({}, {
  get: (_target: unknown, prop: string) => {
    const Component = React.forwardRef(
      ({ children, ...rest }: Record<string, unknown>, ref: React.Ref<unknown>) => {
        const domProps = Object.fromEntries(
          Object.entries(rest).filter(([key]) => !MOTION_SPECIFIC_PROPS.has(key))
        )
        return React.createElement(prop, { ...domProps, ref }, children as React.ReactNode)
      }
    )
    Component.displayName = `Motion.${String(prop)}`
    return Component
  }
})

export const AnimatePresence = ({ children }: { children: React.ReactNode }) => <>{children}</>

// 透传：不执行动画相关逻辑（reducedMotion 等配置 prop 直接剥离）。
export const MotionConfig = ({ children }: { children: React.ReactNode }) => <>{children}</>
