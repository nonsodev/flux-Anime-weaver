import modal
import os
import random
import io
import base64
from PIL import Image
from typing import Optional, Tuple

# Configuration constants
class Config:
    MODEL_NAME = "black-forest-labs/FLUX.1-schnell"
    GPU_TYPE = "A100-40GB"
    TIMEOUT = 600
    DEFAULT_STEPS = 4
    MAX_STEPS = 8
    DEFAULT_SEED = -1
    IMAGE_FORMAT = "PNG"
    MAX_SEQUENCE_LENGTH = 256
    
    # Style configurations
    ANIME_STYLE_SUFFIX = ", **anime style, vibrant colors, fantastical, cinematic lighting, highly detailed, studio quality, trending on ArtStation.**"
    
    # Default prompt with preview image
    DEFAULT_PROMPT = "A serene girl standing under a cherry blossom tree"
    DEFAULT_IMAGE_FILE = "/assets/cherry_blossom_girl.png"
    
    # Simple prompt suggestions (text only)
    PROMPT_SUGGESTIONS = [
        "An ancient samurai meditating by a waterfall",
        "A dragon soaring through cloudy skies",
        "A cozy café scene with anime characters"
    ]

# Define the image with required dependencies
image = modal.Image.debian_slim(python_version="3.11").pip_install([
    "diffusers",
    "torch", 
    "torchvision",
    "transformers",
    "accelerate",
    "safetensors",
    "sentencepiece",
    "protobuf",
    "fastapi",
    "pillow",
    "python-multipart"
]).add_local_dir(os.path.join(os.getcwd(),"assets"),"/assets", copy=True)

app = modal.App("flux-anime-weaver")

class ImageGenerator:
    """Handles image generation logic"""
    
    def __init__(self):
        self.pipe = None
    
    def load_model(self):
        """Load the FLUX model"""
        if self.pipe is None:
            from diffusers import FluxPipeline
            import torch
            
            print(f"Loading FLUX model: {Config.MODEL_NAME}")
            self.pipe = FluxPipeline.from_pretrained(
                Config.MODEL_NAME,
                torch_dtype=torch.bfloat16,
                token=os.getenv("HF_TOKEN")
            ).to("cuda")
            print("Model loaded successfully")
    
    def generate(self, prompt: str, seed: int = Config.DEFAULT_SEED, steps: int = Config.DEFAULT_STEPS) -> Tuple[str, int]:
        """Generate image from prompt"""
        import torch
        
        self.load_model()
        
        # Handle random seed
        if seed == Config.DEFAULT_SEED:
            seed = random.randint(0, 2**32 - 1)
        
        generator = torch.Generator(device="cuda").manual_seed(seed)
        
        print(f"Generating image - Steps: {steps}, Seed: {seed}")
        print(f"Prompt: {prompt[:150]}...")
        
        result = self.pipe(
            prompt,
            guidance_scale=0.0,
            num_inference_steps=steps,
            max_sequence_length=Config.MAX_SEQUENCE_LENGTH,
            generator=generator
        ).images[0]
        
        # Convert to base64
        buffered = io.BytesIO()
        result.save(buffered, format=Config.IMAGE_FORMAT)
        img_str = base64.b64encode(buffered.getvalue()).decode()
        
        print(f"Image generated successfully - Size: {len(img_str)} bytes")
        return img_str, seed

class PromptProcessor:
    """Handles prompt processing and enhancement"""
    
    @staticmethod
    def enhance_prompt(user_prompt: str, style_suffix: str = Config.ANIME_STYLE_SUFFIX) -> str:
        """Add style suffix to user prompt"""
        return user_prompt.strip() + style_suffix
    
    @staticmethod
    def validate_prompt(prompt: str) -> bool:
        """Validate prompt length and content"""
        if not prompt or len(prompt.strip()) == 0:
            return False
        if len(prompt) > 1000:  # Reasonable limit
            return False
        return True

def load_default_image(filename: str) -> Optional[str]:
    """Load the default preview image file and convert to base64"""
    try:
        if os.path.exists(filename):
            with open(filename, "rb") as f:
                img_data = f.read()
                return base64.b64encode(img_data).decode()
        else:
            print(f"Warning: Default image {filename} not found")
            return None
    except Exception as e:
        print(f"Error loading default image {filename}: {e}")
        return None

# Global image generator instance
generator = ImageGenerator()

@app.function(
    image=image,
    gpu=Config.GPU_TYPE,
    timeout=Config.TIMEOUT,
    secrets=[modal.Secret.from_name("huggingface-token")],
    # min_containers=1,  # Keep one instance warm for faster response
)
def generate_image(prompt: str, seed: int = Config.DEFAULT_SEED, steps: int = Config.DEFAULT_STEPS):
    """Core image generation function"""
    try:
        # Validate inputs
        if not PromptProcessor.validate_prompt(prompt):
            raise ValueError("Invalid prompt provided")
        
        if not (1 <= steps <= Config.MAX_STEPS):
            steps = Config.DEFAULT_STEPS
            
        # Generate image
        img_str, used_seed = generator.generate(prompt, seed, steps)
        
        return {
            "success": True,
            "image": img_str,
            "seed": used_seed,
            "steps": steps,
            "prompt": prompt[:100] + "..." if len(prompt) > 100 else prompt
        }
        
    except Exception as e:
        print(f"Error in generate_image: {e}")
        return {
            "success": False,
            "error": str(e),
            "seed": seed,
            "steps": steps
        }

@app.function(image=image)
@modal.asgi_app()
def web_app():
    """Enhanced web interface"""
    from fastapi import FastAPI, Form, HTTPException
    from fastapi.responses import HTMLResponse, JSONResponse
    from fastapi.middleware.cors import CORSMiddleware
    
    app = FastAPI(title="FLUX Anime Dream Weaver", version="2.0")
    
    # Add CORS middleware for development
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return get_html_interface()
    
    @app.get("/health")
    async def health_check():
        """Health check endpoint"""
        return {"status": "healthy", "model": Config.MODEL_NAME}
    
    @app.post("/generate")
    async def generate_endpoint(
        prompt: str = Form(...),
        steps: int = Form(Config.DEFAULT_STEPS),
        seed: int = Form(Config.DEFAULT_SEED)
    ):
        """Generate image endpoint with enhanced error handling"""
        try:
            # Validate inputs
            if not PromptProcessor.validate_prompt(prompt):
                raise HTTPException(status_code=400, detail="Invalid prompt")
            
            if not (1 <= steps <= Config.MAX_STEPS):
                steps = Config.DEFAULT_STEPS
            
            # Enhance prompt
            enhanced_prompt = PromptProcessor.enhance_prompt(prompt)
            
            # Generate image
            result = generate_image.remote(enhanced_prompt, seed, steps)
            
            if result["success"]:
                return JSONResponse({
                    "success": True,
                    "image": result["image"],
                    "seed": result["seed"],
                    "steps": result["steps"],
                    "original_prompt": prompt,
                    "enhanced_prompt": enhanced_prompt[:100] + "..." if len(enhanced_prompt) > 100 else enhanced_prompt
                })
            else:
                return JSONResponse({
                    "success": False,
                    "error": result["error"]
                }, status_code=500)
                
        except Exception as e:
            return JSONResponse({
                "success": False,
                "error": f"Server error: {str(e)}"
            }, status_code=500)
    
    @app.get("/default-preview")
    async def get_default_preview():
        """Get the default preview image for the cherry blossom prompt"""
        default_image = load_default_image(Config.DEFAULT_IMAGE_FILE)
        return {
            "prompt": Config.DEFAULT_PROMPT,
            "image": default_image
        }
    
    return app

def get_html_interface() -> str:
    """Generate the enhanced HTML interface with modern UI"""
    # Create simple suggestion tags (text only)
    suggestions_html = ""
    for suggestion in Config.PROMPT_SUGGESTIONS:
        suggestions_html += f"""<div class="suggestion-card" onclick="setPrompt('{suggestion}')">
            <div class="suggestion-icon">✨</div>
            <div class="suggestion-text">{suggestion}</div>
        </div>"""
    
    # Load default preview image for the initial prompt
    default_image = load_default_image(Config.DEFAULT_IMAGE_FILE)
    default_image_data = f"data:image/png;base64,{default_image}" if default_image else ""
    
    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>🌸 FLUX Anime Dream Weaver</title>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=Space+Grotesk:wght@500;700&display=swap" rel="stylesheet">
        <style>
            :root {{
                --primary: #6366f1;
                --primary-light: #818cf8;
                --primary-dark: #4f46e5;
                --secondary: #ec4899;
                --secondary-light: #f472b6;
                --accent: #06b6d4;
                --success: #10b981;
                --warning: #f59e0b;
                --error: #ef4444;
                
                --bg-primary: #0f0f23;
                --bg-secondary: #1a1a2e;
                --bg-tertiary: #16213e;
                --bg-card: rgba(26, 26, 46, 0.8);
                --bg-glass: rgba(255, 255, 255, 0.05);
                
                --text-primary: #ffffff;
                --text-secondary: #a1a1aa;
                --text-muted: #71717a;
                
                --border: rgba(255, 255, 255, 0.1);
                --border-focus: rgba(99, 102, 241, 0.5);
                
                --shadow-sm: 0 4px 6px -1px rgba(0, 0, 0, 0.3);
                --shadow-md: 0 10px 15px -3px rgba(0, 0, 0, 0.4);
                --shadow-lg: 0 25px 50px -12px rgba(0, 0, 0, 0.6);
                --shadow-glow: 0 0 20px rgba(99, 102, 241, 0.4);
                
                --radius-sm: 8px;
                --radius-md: 12px;
                --radius-lg: 16px;
                --radius-xl: 24px;
            }}

            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}

            body {{
                font-family: 'Inter', sans-serif;
                background: var(--bg-primary);
                color: var(--text-primary);
                min-height: 100vh;
                overflow-x: hidden;
                position: relative;
            }}

            /* Animated background */
            body::before {{
                content: '';
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                background: 
                    radial-gradient(circle at 20% 20%, rgba(99, 102, 241, 0.15) 0%, transparent 50%),
                    radial-gradient(circle at 80% 80%, rgba(236, 72, 153, 0.15) 0%, transparent 50%),
                    radial-gradient(circle at 40% 60%, rgba(6, 182, 212, 0.1) 0%, transparent 50%);
                animation: backgroundShift 20s ease-in-out infinite;
                z-index: -1;
            }}

            @keyframes backgroundShift {{
                0%, 100% {{ transform: scale(1) rotate(0deg); }}
                33% {{ transform: scale(1.1) rotate(1deg); }}
                66% {{ transform: scale(0.9) rotate(-1deg); }}
            }}

            /* Header */
            .header {{
                position: relative;
                padding: 60px 0 80px;
                text-align: center;
                background: linear-gradient(135deg, 
                    rgba(99, 102, 241, 0.1) 0%, 
                    rgba(236, 72, 153, 0.1) 100%);
                border-bottom: 1px solid var(--border);
                backdrop-filter: blur(20px);
            }}

            .header::before {{
                content: '';
                position: absolute;
                top: 0;
                left: 50%;
                transform: translateX(-50%);
                width: 200%;
                height: 100%;
                background: linear-gradient(45deg, 
                    transparent 30%, 
                    rgba(99, 102, 241, 0.05) 50%, 
                    transparent 70%);
                animation: headerShine 8s ease-in-out infinite;
            }}

            @keyframes headerShine {{
                0%, 100% {{ transform: translateX(-50%) skewX(-15deg); }}
                50% {{ transform: translateX(-30%) skewX(-15deg); }}
            }}

            .header-content {{
                position: relative;
                z-index: 2;
            }}

            .header h1 {{
                font-family: 'Space Grotesk', sans-serif;
                font-size: clamp(2.5rem, 5vw, 4.5rem);
                font-weight: 700;
                background: linear-gradient(135deg, var(--primary-light), var(--secondary));
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                background-clip: text;
                margin-bottom: 20px;
                text-shadow: 0 0 30px rgba(99, 102, 241, 0.3);
                animation: titleFloat 6s ease-in-out infinite;
            }}

            @keyframes titleFloat {{
                0%, 100% {{ transform: translateY(0px); }}
                50% {{ transform: translateY(-10px); }}
            }}

            .header p {{
                font-size: 1.2rem;
                color: var(--text-secondary);
                font-weight: 400;
                max-width: 600px;
                margin: 0 auto;
                line-height: 1.6;
            }}

            /* Main container */
            .container {{
                max-width: 1400px;
                margin: 0 auto;
                padding: 60px 20px;
                display: grid;
                grid-template-columns: 500px 1fr;
                gap: 60px;
                align-items: start;
            }}

            /* Form section */
            .form-section {{
                background: var(--bg-card);
                backdrop-filter: blur(20px);
                border: 1px solid var(--border);
                border-radius: var(--radius-xl);
                padding: 40px;
                box-shadow: var(--shadow-lg);
                position: sticky;
                top: 40px;
                transition: all 0.3s ease;
            }}

            .form-section:hover {{
                border-color: var(--border-focus);
                box-shadow: var(--shadow-lg), var(--shadow-glow);
            }}

            .form-group {{
                margin-bottom: 32px;
            }}

            .form-group label {{
                display: block;
                margin-bottom: 12px;
                font-weight: 600;
                color: var(--text-primary);
                font-size: 0.95rem;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }}

            .input-wrapper {{
                position: relative;
            }}

            .form-group input,
            .form-group select {{
                width: 100%;
                padding: 16px 20px;
                background: var(--bg-glass);
                border: 1px solid var(--border);
                border-radius: var(--radius-md);
                color: var(--text-primary);
                font-size: 1rem;
                font-family: inherit;
                transition: all 0.3s ease;
                backdrop-filter: blur(10px);
            }}

            .form-group input:focus,
            .form-group select:focus {{
                outline: none;
                border-color: var(--primary);
                box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.2);
                transform: translateY(-2px);
            }}

            .form-group input::placeholder {{
                color: var(--text-muted);
            }}

            /* Suggestions */
            .suggestions-grid {{
                display: grid;
                grid-template-columns: 1fr;
                gap: 12px;
                margin-top: 16px;
            }}

            .suggestion-card {{
                display: flex;
                align-items: center;
                gap: 12px;
                padding: 16px;
                background: var(--bg-glass);
                border: 1px solid var(--border);
                border-radius: var(--radius-md);
                cursor: pointer;
                transition: all 0.3s ease;
                backdrop-filter: blur(10px);
            }}

            .suggestion-card:hover {{
                border-color: var(--primary);
                background: rgba(99, 102, 241, 0.1);
                transform: translateY(-2px);
                box-shadow: var(--shadow-md);
            }}

            .suggestion-icon {{
                font-size: 1.2rem;
                opacity: 0.8;
            }}

            .suggestion-text {{
                font-size: 0.9rem;
                color: var(--text-secondary);
                font-weight: 500;
            }}

            /* Generate button */
            .generate-btn {{
                width: 100%;
                padding: 20px;
                background: linear-gradient(135deg, var(--primary), var(--secondary));
                color: white;
                border: none;
                border-radius: var(--radius-md);
                font-size: 1.1rem;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.3s ease;
                position: relative;
                overflow: hidden;
                box-shadow: var(--shadow-md);
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }}

            .generate-btn::before {{
                content: '';
                position: absolute;
                top: 0;
                left: -100%;
                width: 100%;
                height: 100%;
                background: linear-gradient(90deg, 
                    transparent, 
                    rgba(255, 255, 255, 0.2), 
                    transparent);
                transition: left 0.5s ease;
            }}

            .generate-btn:hover {{
                transform: translateY(-3px);
                box-shadow: var(--shadow-lg), 0 0 30px rgba(99, 102, 241, 0.4);
            }}

            .generate-btn:hover::before {{
                left: 100%;
            }}

            .generate-btn:active {{
                transform: translateY(-1px);
            }}

            .generate-btn:disabled {{
                opacity: 0.6;
                cursor: not-allowed;
                transform: none;
            }}

            /* Status */
            .status {{
                padding: 16px 20px;
                border-radius: var(--radius-md);
                font-weight: 500;
                margin-top: 24px;
                border-left: 4px solid var(--primary);
                background: rgba(99, 102, 241, 0.1);
                backdrop-filter: blur(10px);
                transition: all 0.3s ease;
            }}

            .status.generating {{
                border-left-color: var(--warning);
                background: rgba(245, 158, 11, 0.1);
                animation: statusPulse 2s infinite;
            }}

            .status.success {{
                border-left-color: var(--success);
                background: rgba(16, 185, 129, 0.1);
            }}

            .status.error {{
                border-left-color: var(--error);
                background: rgba(239, 68, 68, 0.1);
            }}

            @keyframes statusPulse {{
                0%, 100% {{ opacity: 1; transform: scale(1); }}
                50% {{ opacity: 0.8; transform: scale(1.02); }}
            }}

            /* Result section */
            .result-section {{
                display: flex;
                flex-direction: column;
                gap: 30px;
            }}

            .image-container {{
                background: var(--bg-card);
                backdrop-filter: blur(20px);
                border: 1px solid var(--border);
                border-radius: var(--radius-xl);
                padding: 30px;
                box-shadow: var(--shadow-lg);
                min-height: 600px;
                display: flex;
                align-items: center;
                justify-content: center;
                transition: all 0.3s ease;
                position: relative;
                overflow: hidden;
            }}

            .image-container::before {{
                content: '';
                position: absolute;
                top: 0;
                left: 0;
                right: 0;
                bottom: 0;
                background: linear-gradient(45deg, 
                    rgba(99, 102, 241, 0.05), 
                    rgba(236, 72, 153, 0.05));
                opacity: 0;
                transition: opacity 0.3s ease;
            }}

            .image-container:hover::before {{
                opacity: 1;
            }}

            .image-placeholder {{
                text-align: center;
                color: var(--text-muted);
                z-index: 2;
                position: relative;
            }}

            .placeholder-icon {{
                font-size: 4rem;
                margin-bottom: 20px;
                opacity: 0.6;
                animation: placeholderFloat 3s ease-in-out infinite;
            }}

            @keyframes placeholderFloat {{
                0%, 100% {{ transform: translateY(0px); }}
                50% {{ transform: translateY(-10px); }}
            }}

            .placeholder-text {{
                font-size: 1.2rem;
                margin-bottom: 10px;
                font-weight: 500;
            }}

            .placeholder-subtext {{
                font-size: 1rem;
                opacity: 0.7;
            }}

            .generated-image {{
                max-width: 100%;
                height: auto;
                border-radius: var(--radius-lg);
                box-shadow: var(--shadow-lg);
                transition: all 0.4s ease;
                cursor: zoom-in;
                z-index: 2;
                position: relative;
            }}

            .generated-image:hover {{
                transform: scale(1.05) rotate(1deg);
                box-shadow: var(--shadow-lg), 0 0 40px rgba(99, 102, 241, 0.3);
            }}

            /* Loading */
            .loading-container {{
                text-align: center;
                z-index: 2;
                position: relative;
            }}

            .loading-spinner {{
                width: 60px;
                height: 60px;
                border: 3px solid rgba(99, 102, 241, 0.3);
                border-top: 3px solid var(--primary);
                border-radius: 50%;
                animation: spin 1s linear infinite;
                margin: 0 auto 20px;
            }}

            @keyframes spin {{
                0% {{ transform: rotate(0deg); }}
                100% {{ transform: rotate(360deg); }}
            }}

            .loading-text {{
                font-size: 1.1rem;
                font-weight: 600;
                color: var(--primary);
                margin-bottom: 10px;
            }}

            .loading-subtext {{
                font-size: 0.95rem;
                color: var(--text-muted);
            }}

            /* Image info */
            .image-info {{
                background: var(--bg-glass);
                backdrop-filter: blur(10px);
                border: 1px solid var(--border);
                border-radius: var(--radius-md);
                padding: 20px;
                margin-top: 20px;
                text-align: center;
            }}

            .image-info-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
                gap: 16px;
                margin-bottom: 12px;
            }}

            .info-item {{
                text-align: center;
            }}

            .info-label {{
                font-size: 0.8rem;
                color: var(--text-muted);
                text-transform: uppercase;
                letter-spacing: 0.5px;
                margin-bottom: 4px;
            }}

            .info-value {{
                font-size: 1rem;
                font-weight: 600;
                color: var(--text-primary);
            }}

            /* Advanced options */
            .advanced-toggle {{
                background: none;
                border: none;
                color: var(--primary);
                cursor: pointer;
                font-weight: 500;
                font-size: 0.9rem;
                padding: 8px 0;
                margin-top: 16px;
                transition: all 0.3s ease;
                text-decoration: underline;
                text-decoration-color: transparent;
            }}

            .advanced-toggle:hover {{
                text-decoration-color: var(--primary);
            }}

            .advanced-options {{
                margin-top: 20px;
                padding: 24px;
                background: rgba(99, 102, 241, 0.05);
                border: 1px solid rgba(99, 102, 241, 0.2);
                border-radius: var(--radius-md);
                backdrop-filter: blur(10px);
                display: none;
            }}

            .info-panel {{
                background: rgba(6, 182, 212, 0.1);
                border: 1px solid rgba(6, 182, 212, 0.2);
                border-radius: var(--radius-sm);
                padding: 12px 16px;
                margin-top: 12px;
                font-size: 0.85rem;
                color: var(--text-secondary);
            }}

            /* Responsive */
            @media (max-width: 1024px) {{
                .container {{
                    grid-template-columns: 1fr;
                    gap: 40px;
                    padding: 40px 20px;
                }}

                .form-section {{
                    position: static;
                }}
            }}

            @media (max-width: 768px) {{
                .header {{
                    padding: 40px 0 60px;
                }}

                .container {{
                    padding: 30px 15px;
                    gap: 30px;
                }}

                .form-section,
                .image-container {{
                    padding: 24px;
                }}

                .suggestions-grid {{
                    grid-template-columns: 1fr;
                }}

                .image-info-grid {{
                    grid-template-columns: 1fr 1fr;
                }}
            }}

            /* Fullscreen modal */
            .fullscreen-modal {{
                position: fixed;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                background: rgba(0, 0, 0, 0.95);
                z-index: 1000;
                display: none;
                align-items: center;
                justify-content: center;
                backdrop-filter: blur(10px);
            }}

            .fullscreen-modal.active {{
                display: flex;
            }}

            .fullscreen-image {{
                max-width: 95%;
                max-height: 95%;
                border-radius: var(--radius-lg);
                box-shadow: var(--shadow-lg);
            }}

            .close-fullscreen {{
                position: absolute;
                top: 30px;
                right: 30px;
                background: rgba(255, 255, 255, 0.1);
                border: none;
                color: white;
                font-size: 2rem;
                width: 50px;
                height: 50px;
                border-radius: 50%;
                cursor: pointer;
                transition: all 0.3s ease;
                backdrop-filter: blur(10px);
            }}

            .close-fullscreen:hover {{
                background: rgba(255, 255, 255, 0.2);
                transform: scale(1.1);
            }}
        </style>
    </head>
    <body>
        <div class="header">
            <div class="header-content">
                <h1>🌸 FLUX Anime Dream Weaver</h1>
                <p>Transform your imagination into stunning anime artwork with cutting-edge AI</p>
            </div>
        </div>

        <div class="container">
            <div class="form-section">
                <form id="generateForm">
                    <div class="form-group">
                        <label for="prompt">✨ Your Creative Vision</label>
                        <div class="input-wrapper">
                            <input 
                                type="text" 
                                id="prompt" 
                                placeholder="Describe your anime scene..." 
                                value="{Config.DEFAULT_PROMPT}" 
                                required
                            >
                        </div>
                        
                        <div class="suggestions-grid">
                            {suggestions_html}
                        </div>
                        
                        <div class="info-panel">
                            <strong>✨ Auto-Enhancement:</strong> Every prompt is automatically enhanced with anime styling for optimal results!
                        </div>
                    </div>

                    <div class="form-group">
                        <label for="steps">🎯 Quality Level</label>
                        <select id="steps">
                            <option value="1">Lightning Fast ⚡ (1 step)</option>
                            <option value="2">Quick Draft 🚀 (2 steps)</option>
                            <option value="3">Good Quality ⚖️ (3 steps)</option>
                            <option value="4" selected>High Quality ✨ (4 steps)</option>
                            <option value="5">Premium 🏆 (5 steps)</option>
                            <option value="6">Studio Grade 💎 (6 steps)</option>
                            <option value="7">Masterpiece 🌟 (7 steps)</option>
                            <option value="8">Ultra Detail 🔥 (8 steps)</option>
                        </select>
                    </div>

                    <button type="button" class="advanced-toggle" onclick="toggleAdvanced()">
                        ⚙️ Advanced Options
                    </button>
                    
                    <div class="advanced-options" id="advancedOptions">
                        <div class="form-group">
                            <label for="seed">🎲 Seed Control</label>
                            <input 
                                type="number" 
                                id="seed" 
                                placeholder="Enter seed for reproducible results" 
                                value="-1"
                            >
                            <div class="info-panel">
                                Use -1 for random generation, or enter a specific number to reproduce previous results
                            </div>
                        </div>
                    </div>

                    <button type="submit" class="generate-btn" id="generateBtn">
                        🎨 Create Anime Art
                    </button>
                </form>

                <div class="status" id="status">🚀 Ready to bring your creative vision to life!</div>
            </div>

            <div class="result-section">
                <div class="image-container">
                    <div id="imageContainer">
                        {f'''
                        <div style="text-align: center; width: 100%;">
                            <img src="{default_image_data}" alt="Preview" class="generated-image" onclick="openFullscreen(this)">
                            <div class="image-info">
                                <div class="image-info-grid">
                                    <div class="info-item">
                                        <div class="info-label">Prompt</div>
                                        <div class="info-value">Preview</div>
                                    </div>
                                    <div class="info-item">
                                        <div class="info-label">Status</div>
                                        <div class="info-value">Ready</div>
                                    </div>
                                </div>
                                <div style="font-size: 0.85rem; color: var(--text-muted); margin-top: 8px;">
                                    Click "Create Anime Art" to generate your masterpiece
                                </div>
                            </div>
                        </div>
                        ''' if default_image else '''
                        <div class="image-placeholder">
                            <div class="placeholder-icon">🌸</div>
                            <div class="placeholder-text">Your anime masterpiece will appear here</div>
                            <div class="placeholder-subtext">Enter your vision and click "Create Anime Art" to begin</div>
                        </div>
                        '''}
                    </div>
                </div>
            </div>
        </div>

        <!-- Fullscreen modal -->
        <div class="fullscreen-modal" id="fullscreenModal" onclick="closeFullscreen()">
            <button class="close-fullscreen" onclick="closeFullscreen()">×</button>
            <img class="fullscreen-image" id="fullscreenImage" onclick="event.stopPropagation()">
        </div>

        <script>
            // Set prompt function
            function setPrompt(promptText) {{
                document.getElementById('prompt').value = promptText;
                
                const status = document.getElementById('status');
                status.textContent = `✨ Prompt set! Ready to generate "${{promptText.length > 30 ? promptText.substring(0, 30) + '...' : promptText}}".`;
                status.className = 'status';
                
                // Add subtle animation to form
                const formSection = document.querySelector('.form-section');
                formSection.style.transform = 'scale(1.02)';
                setTimeout(() => {{
                    formSection.style.transform = 'scale(1)';
                }}, 200);
            }}

            // Toggle advanced options
            function toggleAdvanced() {{
                const advanced = document.getElementById('advancedOptions');
                const button = document.querySelector('.advanced-toggle');
                
                if (advanced.style.display === 'none' || advanced.style.display === '') {{
                    advanced.style.display = 'block';
                    button.textContent = '⚙️ Hide Advanced Options';
                }} else {{
                    advanced.style.display = 'none';
                    button.textContent = '⚙️ Advanced Options';
                }}
            }}

            // Fullscreen functions
            function openFullscreen(img) {{
                const modal = document.getElementById('fullscreenModal');
                const fullscreenImg = document.getElementById('fullscreenImage');
                fullscreenImg.src = img.src;
                modal.classList.add('active');
                document.body.style.overflow = 'hidden';
            }}

            function closeFullscreen() {{
                const modal = document.getElementById('fullscreenModal');
                modal.classList.remove('active');
                document.body.style.overflow = 'auto';
            }}

            // Escape key to close fullscreen
            document.addEventListener('keydown', (e) => {{
                if (e.key === 'Escape') {{
                    closeFullscreen();
                }}
            }});

            // Form submission
            document.getElementById('generateForm').addEventListener('submit', async (e) => {{
                e.preventDefault();

                const userPrompt = document.getElementById('prompt').value;
                const steps = document.getElementById('steps').value;
                const seed = document.getElementById('seed').value;
                const status = document.getElementById('status');
                const imageContainer = document.getElementById('imageContainer');
                const generateBtn = document.getElementById('generateBtn');

                // Update UI for generation state
                generateBtn.disabled = true;
                generateBtn.textContent = '🎨 Creating...';
                status.textContent = `🎯 Generating your anime artwork with ${{steps}} quality steps...`;
                status.className = 'status generating';

                imageContainer.innerHTML = `
                    <div class="loading-container">
                        <div class="loading-spinner"></div>
                        <div class="loading-text">Crafting your anime masterpiece...</div>
                        <div class="loading-subtext">This magical process takes 30-90 seconds</div>
                    </div>
                `;

                try {{
                    const formData = new FormData();
                    formData.append('prompt', userPrompt);
                    formData.append('steps', steps);
                    formData.append('seed', seed);

                    const response = await fetch('/generate', {{
                        method: 'POST',
                        body: formData
                    }});

                    const result = await response.json();

                    if (result.success) {{
                        imageContainer.innerHTML = `
                            <div style="text-align: center; width: 100%;">
                                <img src="data:image/png;base64,${{result.image}}" alt="Generated anime art" class="generated-image" onclick="openFullscreen(this)">
                                <div class="image-info">
                                    <div class="image-info-grid">
                                        <div class="info-item">
                                            <div class="info-label">Seed</div>
                                            <div class="info-value">${{result.seed}}</div>
                                        </div>
                                        <div class="info-item">
                                            <div class="info-label">Steps</div>
                                            <div class="info-value">${{result.steps}}</div>
                                        </div>
                                        <div class="info-item">
                                            <div class="info-label">Status</div>
                                            <div class="info-value">Complete</div>
                                        </div>
                                    </div>
                                    <div style="font-size: 0.85rem; color: var(--text-muted); margin-top: 8px;">
                                        Click image for fullscreen view • Use seed ${{result.seed}} to recreate this result
                                    </div>
                                </div>
                            </div>
                        `;
                        status.textContent = `✅ Your anime masterpiece is complete!`;
                        status.className = 'status success';
                    }} else {{
                        throw new Error(result.error || 'Generation failed');
                    }}
                }} catch (error) {{
                    status.textContent = `❌ Generation failed: ${{error.message}}`;
                    status.className = 'status error';
                    imageContainer.innerHTML = `
                        <div class="image-placeholder">
                            <div class="placeholder-icon">❌</div>
                            <div class="placeholder-text">Generation failed</div>
                            <div class="placeholder-subtext">Please try again with a different prompt or settings</div>
                        </div>
                    `;
                }} finally {{
                    // Reset button state
                    generateBtn.disabled = false;
                    generateBtn.textContent = '🎨 Create Anime Art';
                }}
            }});

            // Add floating animation to suggestions on hover
            document.querySelectorAll('.suggestion-card').forEach(card => {{
                card.addEventListener('mouseenter', () => {{
                    card.style.transform = 'translateY(-4px) scale(1.02)';
                }});
                
                card.addEventListener('mouseleave', () => {{
                    card.style.transform = 'translateY(0) scale(1)';
                }});
            }});

            // Add ripple effect to generate button
            document.getElementById('generateBtn').addEventListener('click', function(e) {{
                const ripple = document.createElement('span');
                const rect = this.getBoundingClientRect();
                const size = Math.max(rect.width, rect.height);
                const x = e.clientX - rect.left - size / 2;
                const y = e.clientY - rect.top - size / 2;
                
                ripple.style.cssText = `
                    position: absolute;
                    width: ${{size}}px;
                    height: ${{size}}px;
                    left: ${{x}}px;
                    top: ${{y}}px;
                    background: rgba(255, 255, 255, 0.3);
                    border-radius: 50%;
                    transform: scale(0);
                    animation: ripple 0.6s ease-out;
                    pointer-events: none;
                `;
                
                this.appendChild(ripple);
                
                setTimeout(() => {{
                    ripple.remove();
                }}, 600);
            }});

            // Add CSS for ripple animation
            const style = document.createElement('style');
            style.textContent = `
                @keyframes ripple {{
                    to {{
                        transform: scale(2);
                        opacity: 0;
                    }}
                }}
                
                .generate-btn {{
                    position: relative;
                    overflow: hidden;
                }}
            `;
            document.head.appendChild(style);

            // Smooth scroll animations for form interactions
            document.getElementById('prompt').addEventListener('focus', () => {{
                document.querySelector('.form-section').style.boxShadow = 'var(--shadow-lg), var(--shadow-glow)';
            }});

            document.getElementById('prompt').addEventListener('blur', () => {{
                document.querySelector('.form-section').style.boxShadow = 'var(--shadow-lg)';
            }});

            // Health check and initialization
            fetch('/health')
                .then(response => response.json())
                .then(data => {{
                    console.log('✅ App health check passed:', data);
                    
                    // Update status with connection confirmation
                    const status = document.getElementById('status');
                    if (status.textContent.includes('Ready to bring')) {{
                        status.textContent = '🚀 Connected and ready! Enter your prompt to begin creating.';
                    }}
                }})
                .catch(error => {{
                    console.log('❌ Health check failed:', error);
                    const status = document.getElementById('status');
                    status.textContent = '⚠️ Connection issue. Please refresh the page.';
                    status.className = 'status error';
                }});

            // Add subtle animations to page load
            window.addEventListener('load', () => {{
                const elements = document.querySelectorAll('.form-section, .image-container');
                elements.forEach((el, index) => {{
                    el.style.opacity = '0';
                    el.style.transform = 'translateY(30px)';
                    
                    setTimeout(() => {{
                        el.style.transition = 'all 0.8s ease';
                        el.style.opacity = '1';
                        el.style.transform = 'translateY(0)';
                    }}, index * 200);
                }});
            }});

            // Easter egg: konami code for special animation
            let konamiCode = [];
            const konamiSequence = ['ArrowUp', 'ArrowUp', 'ArrowDown', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'ArrowLeft', 'ArrowRight', 'KeyB', 'KeyA'];
            
            document.addEventListener('keydown', (e) => {{
                konamiCode.push(e.code);
                if (konamiCode.length > konamiSequence.length) {{
                    konamiCode.shift();
                }}
                
                if (JSON.stringify(konamiCode) === JSON.stringify(konamiSequence)) {{
                    // Special animation
                    document.body.style.animation = 'rainbow 2s ease-in-out';
                    setTimeout(() => {{
                        document.body.style.animation = '';
                    }}, 2000);
                    
                    const style = document.createElement('style');
                    style.textContent = `
                        @keyframes rainbow {{
                            0% {{ filter: hue-rotate(0deg); }}
                            100% {{ filter: hue-rotate(360deg); }}
                        }}
                    `;
                    document.head.appendChild(style);
                    setTimeout(() => style.remove(), 2000);
                    
                    konamiCode = [];
                }}
            }});
        </script>
    </body>
    </html>
    """

if __name__ == "__main__":
    print("Deploy with: modal deploy main.py")
    print("For development: modal serve main.py")