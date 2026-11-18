from ModelLoader import gemini_llm, nemotron_llm

llm_gemini = gemini_llm(max_tokens=1024)
#print("gemini:", llm_gemini.invoke("What is the capital of France?").content)

llm_nemotron = nemotron_llm(max_tokens=1024)
print("nemotron:", llm_nemotron.invoke("What is the transformers model explain 4 lines ").content) 

