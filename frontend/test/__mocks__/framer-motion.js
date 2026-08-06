import React from 'react'

// 与 framer-motion.tsx 等价的 JS 版 mock（vitest 按扩展名解析其一）。
const MOTION_SPECIFIC_PROPS = new Set([
  'initial', 'animate', 'exit', 'transition', 'variants',
  'whileHover', 'whileTap', 'layout',
])

const motion = new Proxy({}, {
  get: (_target, prop) => {
    const Component = React.forwardRef(({ children, ...rest }, ref) => {
      const domProps = Object.fromEntries(
        Object.entries(rest).filter(([key]) => !MOTION_SPECIFIC_PROPS.has(key))
      )
      return React.createElement(prop, { ...domProps, ref }, children)
    })
    Component.displayName = `Motion.${prop}`
    return Component
  }
})

const AnimatePresence = ({ children }) => React.createElement(React.Fragment, null, children)

export { motion, AnimatePresence }
