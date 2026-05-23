import os

class LLM:
    def __init__(self, 
                 model="gpt-4o", 
                 temperature=0, 
                 stateless=True, 
                 state_messages=None):
        """
        Initialize the LLM instance with model details, environment configuration, and optional state parameters.

        Args:
            model (str): Model name.
            temperature (float): Controls the creativity of LLM output. Ranges from 0 to 2.
            stateless (bool): Whether the LLM instance should be stateless (without memory).
            state_messages (list | None): Previous memory context for stateful instances.
        """
        self.model_name = model
        self.temperature = temperature if self.model_name!="o1-mini" else 1
        self.stateless = stateless
        self.memory = None if stateless else self.attach_memory(state_messages)

        if model.startswith("gemini"):
            self.client = gemini_config(self.temperature)
            self.api_key = os.getenv("GEMINI_API_KEY")
        else:
            self.client, self.pricing = openai_config(self.model_name)

        self.logs = {
            'api_calls': {
                'chat': 0,
                'visionchat': 0,
                'textgen': 0,
                'embeddings': 0
            },
            'prompt_tokens': 0,
            'completion_tokens': 0,
            'total_tokens': 0,
            'cost': 0
        }
    
    def get_config(self):
        """
        Return model configuration for use with the Autogen agentic library.

        Returns:
            dict: A dictionary containing model configuration including base URL, API key, etc.
        """
        if self.model_name.startswith("gemini"):
            return {
                "model": self.model_name,
                "api_key": self.api_key,
                "api_type": "google"
            }
        else:
            return {
                "model": self.model_name, 
                "api_key": os.getenv("OPENAI_API_KEY")
            }
    
    def get_usage(self):
        return self.logs
    
    def update_logs(self, tokens_info):
        self.logs = add_dicts(self.logs, tokens_info)


    def attach_memory(self, state_messages=None):
        """
        Attach memory for stateful conversations.

        Args:
            state_messages (list): Previous memory context for stateful instances.

        Returns:
            list: A list representing the conversation memory.
        """
        if self.stateless:
            raise ValueError("Chat memory cannot be attached to a stateless LLM instance.")
        return state_messages if state_messages is not None else []

    def chat(self, user_query, messages=None, temperature=None):
        """
        Execute a chat interaction with the LLM. To access the AzureOpenAI object, use the 'client' object of the class.

        Args:
            user_query (str): The user's query.
            messages (list): Messages sent to the LLM right before the user query - can include system prompts or fewshot examples.
                For enabling conversation memory - use the stateless and state_messages arguments during LLM initialisation.

        Returns:
            raw_output (str): The model's response.
            tokens_info (dict): The model's token usage details.
        
        Raises:
            AssertionError: If the model does not support chat completion.
        """

        allowed_models = [
            'gpt-4o',
            'gpt-4o-mini',
            'o1-mini',
            'gpt-4-turbo',
            'gpt-4',
            'gpt-3.5-turbo',
            "gemini-1.5-flash-8b", 
            "gemini-1.5-flash", 
            "gemini-1.5-pro"
        ]
        assert self.model_name in allowed_models, f"""
        Chat completions functionality does not work with model_name = {self.model_name}. Please try with one of the following: 
        {" | ".join(allowed_models)}""".strip("\n").strip()
        
        if messages is None:
            messages = []
        
        # Incorporate memory if stateful instance
        if not self.stateless and self.memory:
            instance = self.memory
        else:
            instance = []

        instance.extend(messages)
        instance.append({"role": "user", "content": user_query})

        if self.model_name.startswith("gemini"):
            instance, sys_prompt = convert_inputs(instance, self.model_name)
            response = self.client.generate_content(
                instance,
                )
            raw_output = response.text
            tokens_info = {
                'api_calls': {
                    'chat': 1
                },
                "total_tokens": response.usage_metadata.total_token_count,
                "prompt_tokens": response.usage_metadata.prompt_token_count,
                "completion_tokens": 0,
                "cost": (
                    0 * response.usage_metadata.prompt_token_count
                    + 0 * 0
                ),
            }
        else:
            response = self.client.chat.completions.create(
                temperature=temperature if temperature is not None else self.temperature,
                model=self.model_name,
                messages=instance,
            )

            raw_output = response.choices[0].message.content
            tokens_info = {
                'api_calls': {
                    'chat': 1
                },
                "total_tokens": response.usage.total_tokens,
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "cost": (
                    self.pricing["input"] * response.usage.prompt_tokens
                    + self.pricing["output"] * response.usage.completion_tokens
                ),
            }

        self.update_logs(tokens_info)

        # Update memory if stateful instance
        if not self.stateless:
            self.memory = instance

        return raw_output, tokens_info
    
    def visionchat(self, img_data, prompt, img_type='base64', messages=None, temperature=None):
        """
        Execute a vision chat interaction with the LLM. To access the AzureOpenAI object, use the 'client' object of the class.

        Args:
            img_data (str): The image data in base64 or URL format.
            prompt (str): The prompt to go along with the image data.
            img_type (str): 'base64' or 'url'
            messages (list): Messages sent to the LLM right before the user query - can include system prompts or fewshot examples.
                For enabling conversation memory - use the stateless and state_messages arguments during LLM initialisation.

        Returns:
            raw_output (str): The model's response.
            tokens_info (dict): The model's token usage details.
        
        Raises:
            AssertionError: If the model does not support chat completion.
        """

        allowed_models = [
            'o1-mini',
            'gpt-4o', 
            'gpt-4o-mini',
            'gpt-4-turbo',
            "gemini-1.5-flash-8b", 
            "gemini-1.5-flash", 
            "gemini-1.5-pro"
        ]
        assert self.model_name in allowed_models, f"""
        Vision functionality does not work with model_name = {self.model_name}. Please try with one of the following: 
        {" | ".join(allowed_models)}""".strip("\n").strip()
        
        if messages is None:
            messages = []
        
        # Incorporate memory if stateful instance
        if not self.stateless and self.memory:
            instance = self.memory
        else:
            instance = []

        instance.extend(messages)

        if img_type == 'base64':
            img_url = f"data:image/jpeg;base64,{img_data}"
        elif img_type == 'url':
            img_url = img_data

        instance.append({"role": "user", "content": [
            {
                'type': "text",
                'text': prompt
            },
            {
                'type': "image_url",
                'image_url': {
                    'url': img_url
                }
            }
        ]})

        response = self.client.chat.completions.create(
            temperature=temperature if temperature is not None else self.temperature,
            model=self.model_name,
            messages=instance,
        )

        raw_output = response.choices[0].message.content
        tokens_info = {
            'api_calls': {
                'visionchat': 1
            },
            "total_tokens": response.usage.total_tokens,
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "cost": (
                self.pricing["input"] * response.usage.prompt_tokens
                + self.pricing["output"] * response.usage.completion_tokens
            ),
        }

        self.update_logs(tokens_info)

        # Update memory if stateful instance
        if not self.stateless:
            self.memory = instance

        return raw_output, tokens_info

    def generate_text(self, prompt, temperature=None, max_tokens=100):
        """
        Generate text completions for a given prompt.

        Args:
            prompt (str): The input prompt.
            max_tokens (int): Maximum tokens for the completion.

        Returns:
            str: The generated text.

        Raises:
            AssertionError: If the model does not support text generation.
        """

        allowed_models = [
            'gpt-3.5-turbo-instruct', 
            'davinci-002',
            'babbage-002'
        ]
        assert self.model_name in allowed_models, f"""
        Text generation functionality does not work with model_name = {self.model_name}. Please try with one of the following: 
        {" | ".join(allowed_models)}""".strip("\n").strip()

        response = self.client.completions.create(
            model=self.model_name,
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature if temperature is not None else self.temperature,
        )
        tokens_info = {
            'api_calls': {
                'textgen': 1
            },
            "total_tokens": response.usage.total_tokens,
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "cost": (
                self.pricing["input"] * response.usage.prompt_tokens
                + self.pricing["output"] * response.usage.completion_tokens
            ),
        }

        self.update_logs(tokens_info)

        return response.choices[0].text.strip(), tokens_info

    def embeddings(self, input_text):
        """
        Generate embeddings for a given text input.

        Args:
            input_text (str): The text to generate embeddings for.

        Returns:
            list: The embedding vector.
        
        Raises:
            AssertionError: If the model does not support embeddings.
        """

        allowed_models = [
            'text-embedding-3-small', 
            'text-embedding-3-large',
            'text-embedding-ada-002',
            "text-embedding-004"
        ]
        assert self.model_name in allowed_models, f"""
        Embedding functionality does not work with model_name = {self.model_name}. Please try with one of the following: 
        {" | ".join(allowed_models)}""".strip("\n").strip()
        
        if self.model_name == "text-embedding-004":
            response = genai.embed_content(
                model="models/text-embedding-004",
                content=input_text)
            tokens_info = {
                'api_calls': {
                    'embeddings': 1
                },
                "total_tokens": response.usage_metadata.total_token_count,
                "prompt_tokens": response.usage_metadata.prompt_token_count,
                "completion_tokens": 0,
                "cost": (
                    0 * response.usage_metadata.prompt_token_count,
                    + 0 * 0
                ),
            }
        else:
            response = self.client.embeddings.create(
                model=self.model_name,
                input=input_text,
            )
            tokens_info = {
                'api_calls': {
                    'embeddings': 1
                },
                "total_tokens": response.usage.total_tokens,
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": 0,
                "cost": (
                    self.pricing["input"] * response.usage.prompt_tokens
                    + self.pricing["output"] * 0
                ),
            }

        self.update_logs(tokens_info)

        return response.data[0].embedding, tokens_info


def add_dicts(dict1, dict2):
    """Helper function to add nested dictionaries"""
    result = dict1.copy()
    for key, value in dict2.items():
        if isinstance(value, dict) and key in result:
            result[key] = add_dicts(result[key], value)
        else:
            result[key] = result.get(key, 0) + value
    return result


def openai_config(model_name):
    """Configure OpenAI client"""
    from openai import OpenAI
    
    pricing = {
        "gpt-4o": {"input": 2.5e-6, "output": 10e-6},
        "gpt-4o-mini": {"input": 0.15e-6, "output": 0.6e-6},
        "gpt-4-turbo": {"input": 10e-6, "output": 30e-6},
        "gpt-4": {"input": 30e-6, "output": 60e-6},
        "gpt-3.5-turbo": {"input": 0.5e-6, "output": 1.5e-6},
    }
    
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return client, pricing.get(model_name, {"input": 0, "output": 0})


def gemini_config(temperature):
    """Configure Gemini client"""
    import google.generativeai as genai
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    return genai.GenerativeModel('gemini-1.5-flash')


def convert_inputs(messages, model_name):
    """Convert messages format for Gemini"""
    sys_prompt = None
    converted = []
    for msg in messages:
        if msg["role"] == "system":
            sys_prompt = msg["content"]
        else:
            converted.append(msg)
    return converted, sys_prompt
