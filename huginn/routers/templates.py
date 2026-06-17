"""Templates endpoints — extraction template registry."""

import logging

from fastapi import APIRouter, Depends, HTTPException

from ..config import HuginnConfig

logger = logging.getLogger(__name__)


def create_templates_router(config: HuginnConfig, verify_api_key) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/templates", tags=["Templates"])
    async def list_templates_api(auth=Depends(verify_api_key)):
        """List all available extraction template with schemas."""
        from ..templates import get_all_templates
        result = []
        for name, t in get_all_templates().items():
            result.append({
                "name": name,
                "description": t.description,
                "schema": t.schema,
                "fields_guide": t.fields_guide,
                "merge_strategy": t.merge_strategy,
                "max_page_chars": t.max_page_chars,
            })
        return {"success": True, "templates": result, "count": len(result)}

    @router.get("/v1/templates/{template_name}", tags=["Templates"])
    async def get_template_api(template_name: str, auth=Depends(verify_api_key)):
        """Get a single template's full details."""
        from ..templates import get_template
        try:
            t = get_template(template_name)
            return {
                "success": True,
                "name": t.name,
                "description": t.description,
                "schema": t.schema,
                "system_prompt": t.system_prompt,
                "fields_guide": t.fields_guide,
                "merge_strategy": t.merge_strategy,
                "max_page_chars": t.max_page_chars,
            }
        except KeyError:
            raise HTTPException(
                status_code=404,
                detail=f"Template '{template_name}' not found"
            )

    return router