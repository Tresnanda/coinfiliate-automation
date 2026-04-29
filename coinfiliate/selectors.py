from __future__ import annotations

SELECTORS = {
    "login.email":                      'input[type="email"]',
    "login.password":                   'input[type="password"]',
    "login.submit":                     'button[type="submit"]',
    "shoplist.sync_btn":                'button:has-text("Sync Partner Shop")',
    "shoplist.row":                     'table tbody tr',
    "shoplist.edit_action":             'text=Edit',
    "editshop.tab_affiliate_links":     'button:has-text("Affiliate Links")',
    "editshop.sync_affiliate_btn":      'button:has-text("Sync Affiliate Link")',
    "editshop.select_all":              'label:has-text("Select All") input[type="checkbox"]',
    "editshop.selected_data_dd":        'button:has-text("Selected Data")',
    "editshop.edit_selected":           'div[role="menu"] >> text=Edit',
    "modal.root":                       'div[role="dialog"]:has-text("Edit Selected Partner Shop Links")',
    "modal.published_toggle":           'role=switch[name="Published"]',
    "modal.primary_cookie_name":        'label:has-text("Primary Tracking Cookie Name") + * input',
    "modal.checkout_domains_add":       'div:has-text("Checkout Domains") >> button:has-text("Add")',
    "modal.checkout_domain_input_last": 'div:has-text("Checkout Domains") >> input >> nth=-1',
    "modal.tracking_names_add":         'div:has-text("Tracking Cookie Names") >> button:has-text("Add")',
    "modal.tracking_names_input_last":  'div:has-text("Tracking Cookie Names") >> input >> nth=-1',
    "modal.tracking_domains_add":       'div:has-text("Tracking Cookie Domains") >> button:has-text("Add")',
    "modal.tracking_domains_input_last":'div:has-text("Tracking Cookie Domains") >> input >> nth=-1',
    "modal.save_changes":               'button:has-text("Save Changes")',
    "editshop.published_btn":           'button:has-text("Published"):not([aria-expanded])',
    "editshop.update_btn":              'button:has-text("Update")',
    # Sync-modal fields (appears both at shop list and inside Edit)
    "syncmodal.network_select":         'div[role="dialog"] >> text=Network >> xpath=following::*[@role="combobox"][1]',
    "syncmodal.page_input":             'div[role="dialog"] >> input >> near(text="Page")',
    "syncmodal.page_size_input":        'div[role="dialog"] >> input >> near(text="Page Size")',
    "syncmodal.sync_now_btn":           'div[role="dialog"] >> button:has-text("Sync Now")',
}


def sel(key: str) -> str:
    return SELECTORS[key]
