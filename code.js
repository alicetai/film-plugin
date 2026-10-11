// Film Camera — a focused, image-only film simulation for Figma.

figma.showUI(__html__, { width: 400, height: 510, themeColors: true });

let selectedImage = null;

function getImagePaint(node) {
  if (!node || !('fills' in node) || node.fills === figma.mixed || !Array.isArray(node.fills)) return null;
  const index = node.fills.findIndex(function (paint) {
    return paint.type === 'IMAGE' && paint.visible !== false && paint.imageHash;
  });
  if (index === -1) return null;
  return { index: index, paint: node.fills[index] };
}

function describeSelection() {
  const selection = figma.currentPage.selection;
  if (selection.length !== 1) {
    selectedImage = null;
    return {
      valid: false,
      title: selection.length > 1 ? 'Select one image' : 'No image selected',
      detail: selection.length > 1
        ? 'Choose a single layer with an image fill to apply the effect.'
        : 'Select an image layer on the canvas to get started.'
    };
  }

  const node = selection[0];
  const imagePaint = getImagePaint(node);
  if (!imagePaint) {
    selectedImage = null;
    return {
      valid: false,
      title: 'That is not an image',
      detail: 'Film Camera only works with layers that have an image fill.'
    };
  }

  selectedImage = { node: node, imagePaint: imagePaint };
  return {
    valid: true,
    title: node.name || 'Selected image',
    detail: 'Ready for the Gold 800-inspired treatment.'
  };
}

function sendSelection() {
  figma.ui.postMessage({ type: 'selection', selection: describeSelection() });
}

figma.on('selectionchange', sendSelection);
sendSelection();

figma.ui.onmessage = async function (message) {
  if (message.type === 'request-image') {
    const state = describeSelection();
    if (!state.valid || !selectedImage) {
      figma.ui.postMessage({ type: 'error', message: 'Select one image layer, then try again.' });
      return;
    }

    try {
      const image = figma.getImageByHash(selectedImage.imagePaint.paint.imageHash);
      if (!image) throw new Error('The selected image could not be read.');
      const bytes = await image.getBytesAsync();
      figma.ui.postMessage({ type: 'source-image', bytes: bytes, targetId: selectedImage.node.id });
    } catch (error) {
      figma.ui.postMessage({ type: 'error', message: error.message || 'Could not read this image.' });
    }
    return;
  }

  if (message.type === 'apply-image') {
    const state = describeSelection();
    if (!state.valid || !selectedImage || selectedImage.node.id !== message.targetId) {
      figma.ui.postMessage({ type: 'error', message: 'Your selection changed. Select the image and try again.' });
      return;
    }

    try {
      const image = figma.createImage(new Uint8Array(message.bytes));
      const fills = selectedImage.node.fills.slice();
      fills[selectedImage.imagePaint.index] = Object.assign({}, selectedImage.imagePaint.paint, { imageHash: image.hash });
      selectedImage.node.fills = fills;
      figma.ui.postMessage({ type: 'complete', message: 'Film effect applied to ' + (selectedImage.node.name || 'your image') + '.' });
    } catch (error) {
      figma.ui.postMessage({ type: 'error', message: error.message || 'Could not apply the film effect.' });
    }
  }
};
