import { Component } from 'react'

import '../styles/app.css'

/**
 * Top-level render guard.
 *
 * Without this, any exception thrown while rendering unmounts the entire React
 * tree and the operator is left with a blank page and no way to recover short
 * of a reload. The boundary shows a safe, non-diagnostic message instead.
 *
 * It deliberately renders no exception text, stack trace or component internals:
 * those belong in the browser console, not on an operations screen.
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { failed: false }
    this.handleReset = this.handleReset.bind(this)
  }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  componentDidCatch(error, info) {
    // Console only - never rendered, so nothing sensitive reaches the DOM.
    console.error('ResolveAI render error:', error, info?.componentStack)
  }

  handleReset() {
    this.setState({ failed: false })
  }

  render() {
    if (!this.state.failed) return this.props.children

    return (
      <div className="fatal" role="alert">
        <div className="fatal__body">
          <p className="fatal__title mono">INTERFACE ERROR</p>
          <p className="fatal__text">
            The ResolveAI interface could not render. Reload to continue. No
            customer data was changed.
          </p>
          <button type="button" className="btn btn--secondary" onClick={this.handleReset}>
            TRY AGAIN
          </button>
        </div>
      </div>
    )
  }
}