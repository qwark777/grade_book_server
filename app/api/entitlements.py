from fastapi import APIRouter, Depends, HTTPException
from typing import List, Dict, Any
from app.core.security import get_current_user
from app.core.entitlements import check_entitlement, get_school_id_for_user
from app.models.user import User
from app.models.subscription import EntitlementCheck

router = APIRouter()


@router.get("/check/{entitlement_key}", response_model=EntitlementCheck)
async def check_single_entitlement(
    entitlement_key: str,
    school_id: int = None,
    current_user: User = Depends(get_current_user)
):
    """Check if user has access to a specific entitlement"""
    if not school_id:
        school_id = await get_school_id_for_user(current_user)
    
    if not school_id:
        raise HTTPException(status_code=403, detail="No school access")
    
    return await check_entitlement(school_id, entitlement_key, current_user)


@router.get("/check-multiple", response_model=Dict[str, EntitlementCheck])
async def check_multiple_entitlements(
    entitlement_keys: str,  # Comma-separated list
    school_id: int = None,
    current_user: User = Depends(get_current_user)
):
    """Check multiple entitlements at once"""
    if not school_id:
        school_id = await get_school_id_for_user(current_user)
    
    if not school_id:
        raise HTTPException(status_code=403, detail="No school access")
    
    keys = [key.strip() for key in entitlement_keys.split(",")]
    results = {}
    
    for key in keys:
        results[key] = await check_entitlement(school_id, key, current_user)
    
    return results


@router.get("/features", response_model=Dict[str, bool])
async def get_available_features(
    school_id: int = None,
    current_user: User = Depends(get_current_user)
):
    """Get all available features for the current school"""
    if not school_id:
        school_id = await get_school_id_for_user(current_user)
    
    if not school_id:
        raise HTTPException(status_code=403, detail="No school access")
    
    # Common features to check
    features = [
        "analytics.full",
        "analytics.basic", 
        "ai.quota",
        "exports.csv",
        "classes.unlimited",
        "roles.advanced",
        "timetable.changes",
        "attendance.tracking",
        "reports.advanced",
        "sso.enabled",
        "scim.enabled",
        "audit.logs",
        "webhooks",
        "api.access",
        "multi.school",
        "owner.dashboard"
    ]
    
    results = {}
    for feature in features:
        try:
            check = await check_entitlement(school_id, feature, current_user)
            results[feature] = check.has_access
        except:
            results[feature] = False
    
    return results


@router.get("/limits", response_model=Dict[str, Dict[str, Any]])
async def get_usage_limits(
    school_id: int = None,
    current_user: User = Depends(get_current_user)
):
    """Get usage limits and current usage for the school"""
    if not school_id:
        school_id = await get_school_id_for_user(current_user)
    
    if not school_id:
        raise HTTPException(status_code=403, detail="No school access")
    
    # Features with numeric limits
    limit_features = [
        "classes.max",
        "students.max", 
        "teachers.max",
        "ai.quota",
        "exports.csv",
        "storage.mb",
        "messages.daily"
    ]
    
    results = {}
    for feature in limit_features:
        try:
            check = await check_entitlement(school_id, feature, current_user)
            results[feature] = {
                "has_access": check.has_access,
                "current_usage": check.current_usage,
                "limit": check.limit,
                "reason": check.reason
            }
        except:
            results[feature] = {
                "has_access": False,
                "current_usage": None,
                "limit": None,
                "reason": "Error checking entitlement"
            }
    
    return results
