import React from 'react'

const motion = new Proxy({}, {
  get: (_target, prop) => {
    return React.forwardRef(({ children, initial, animate, exit, transition, variants, whileHover, whileTap, layout, ...rest }, ref) => {
      return React.createElement(prop, { ...rest, ref }, children)
    })
  }
})

const AnimatePresence = ({ children }) => React.createElement(React.Fragment, null, children)

export { motion, AnimatePresence }
