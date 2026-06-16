export function downloadJson(filename: string, data: unknown) {
	const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
	downloadBlob(filename, blob);
}

export function downloadText(filename: string, text: string, type: string) {
	downloadBlob(filename, new Blob([text], { type }));
}

function downloadBlob(filename: string, blob: Blob) {
	const url = URL.createObjectURL(blob);
	const link = document.createElement('a');
	link.href = url;
	link.download = filename;
	link.click();
	URL.revokeObjectURL(url);
}
