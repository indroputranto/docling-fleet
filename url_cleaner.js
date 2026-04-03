function cleanWooCommerceURL(url) {
    try {
        // Create URL object
        const urlObj = new URL(url);
        
        // Get all search parameters
        const params = urlObj.searchParams;
        const cleanedParams = new URLSearchParams();
        
        // Keep only wpf_ parameters
        for (const [key, value] of params) {
            if (key.startsWith('wpf_')) {
                cleanedParams.set(key, value);
            }
        }
        
        // Reconstruct URL with only wpf_ parameters
        urlObj.search = cleanedParams.toString();
        
        return urlObj.toString();
    } catch (error) {
        console.error('Error cleaning URL:', error);
        return url;
    }
}

// Example usage
const originalURL = 'https://www.barepets.com/shop/?wpf_fbv=1&wpf_filter_cat_5=123&&utm_source=Klaviyo&utm_medium=flow&utm_campaign=IM_Abandoned%20Cart%20%28ATC%29&utm_id=RBZZ3V&_kx=DwWZA7YzlWDXIcXwznlr4yCTGdA0w5_E6MktxoHTw9g.V59Acp';

const cleanedURL = cleanWooCommerceURL(originalURL);
console.log('Original URL:', originalURL);
console.log('Cleaned URL:', cleanedURL); 