import { createContext, useContext } from 'react'

// 파일을 여는 함수를 화면 어디서든 쓰게 해 주는 컨텍스트 (제공자는 OpenFile.jsx)
export const OpenContext = createContext(() => {})
export const useOpenFile = () => useContext(OpenContext)
